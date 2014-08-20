"""
Copyright (c) 2011, 2012, Regents of the University of California
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions 
are met:

 - Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.
 - Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the
   distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
"AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS 
FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL 
THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, 
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES 
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR 
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) 
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, 
STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) 
ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED 
OF THE POSSIBILITY OF SUCH DAMAGE.
"""
"""
@author Stephen Dawson-Haggerty <stevedh@eecs.berkeley.edu>
"""

import traceback
import operator
import pprint
import time
import logging

import pandas as pd
import numpy as np

from twisted.internet import reactor, threads, defer
from twisted.enterprise import adbapi
from twisted.python import log
import psycopg2

import smap.reporting as reporting
import smap.util as util
import smap.sjson as json
from smap.operators import null
from smap.core import SmapException
import settings

def makeErrback(request_):
    request = request_
    def errBack(outp):
        print "ERRBACK:", outp
        try:
            request.setResponseCode(500)
            request.finish()
        except:
            traceback.print_exc()

def escape_string(s):
    return psycopg2.extensions.QuotedString(s).getquoted()

class ReadingdbPool:
    def __init__(self):
        self.pool = []
        reactor.addSystemEventTrigger('after', 'shutdown', 
                                      self.shutdown)

    def shutdown(self):
        log.msg("ReadingdbPool shutting down:", len(self.pool))
        map(settings.rdb.db_close, self.pool)

    def get(self):
        # print "connect", settings.READINGDB_HOST, settings.READINGDB_PORT
        return settings.rdb.db_open(host=settings.conf['readingdb']['host'],
                           port=settings.conf['readingdb']['port'])
            
    def put(self, conn):
        # self.pool.append(conn)
        settings.rdb.db_close(conn)

try:
    if hasattr(settings, "rdb"):
        rdb_pool
    else:
        log.err("failed to find readingdb module")
except NameError:
    rdb_pool = ReadingdbPool()

class SmapMetadata:
    def __init__(self, db):
        self.db = db    

    @defer.inlineCallbacks
    def add(self, subid, ids, obj):
        """Set the metadata for a Timeseries object
        """
        tic = time.time()
        for path, ts in obj.iteritems():
            if not util.is_string(path):
                raise Exception("Invalid path: " + path)

            tags = ["hstore('Path', %s)" % escape_string(path)]
            for name, val in util.buildkv('', ts):
                if name == 'Readings' or name == 'uuid': continue
                name, val = escape_string(name), escape_string(str(val))
                if not (util.is_string(name) and util.is_string(val)):
                    raise SmapException('Invalid metadata pair: "%s" -> "%s"' % (str(name),
                                                                                 str(val)),
                                        400)
                tags.append("hstore(%s, %s)" % (name, val))

            query = "UPDATE stream SET metadata = metadata || " + " || ".join(tags) + \
                " WHERE uuid = %s" % escape_string(ts['uuid'])

            # skip path updates if no other metadata
            if len(tags) == 1:
                continue
            yield self.db.runOperation(query)
        logging.getLogger('stats').info("Metadata insert took %0.6fs" % (time.time() - tic))

class DateOutOfRangeException(Exception):
    pass

class SmapData:
    """Class to manage entering data in a readingdb instance from a
    report object.

    1. create stream id records, if they do not exist
    2. look up stream ids
    3. defer add to a thread to use blocking c api
    """
    def __init__(self, db):
        self.db = db

    def _add_data_real(self, ids, obj):
        """Send data to a readingdb backend
        """
        r = None
        try:
            r = rdb_pool.get()
            for ts in obj.itervalues():
                data = [(x[0], 0, x[1]) 
                        for x in ts['Readings'] if x[0] > 0]
                max_time_allowed = 1<<63
                for t in data:
                    if t[0] >= max_time_allowed:
                        raise Exception("Timestamp outside acceptable range")
                # print "add", len(data), "to", ids[ts['uuid']], data[0][0]
                while len(data) > 128:
                    settings.rdb.db_add(r, ids[ts['uuid']], data[:128])
                    del data[:128]
                if len(data) > 0:
                    settings.rdb.db_add(r, ids[ts['uuid']], data[:128])
        except:
            raise
        finally:
            if r != None:
                rdb_pool.put(r)
            else:
                raise Exception("Error creating RDB connection!")
        return True

    def _add_data(self, subid, ids, obj):
        """Store the data and metadata contained in a Timeseires
        """
        print "ADD_DATA: ", repr(obj)

        ids = dict(zip(map(operator.itemgetter('uuid'), obj.itervalues()), ids))
        md = SmapMetadata(self.db)
        meta_deferred = md.add(subid, ids, obj)
        data_deferred = threads.deferToThread(self._add_data_real, ids, obj)        
        d = defer.DeferredList([meta_deferred, data_deferred], 
                               fireOnOneErrback=True, consumeErrors=True)
        # propagate the original error... 
        d.addErrback(lambda x: x.value.subFailure)
        return d

    def _run_create(self, uuids, result, newresult, start=None):
        """Chain together the stream creations so we don't exceed database limits"""
        if len(uuids) > 0:
            query = "SELECT " + ','.join(uuids[:100])
            tic = time.time()
            if start: 
                logging.getLogger('stats').info("run create: %0.6fs" % (tic - start))
            d = self.db.runQuery(query)
            d.addCallback(lambda rv: self._run_create(uuids[100:],
                                                      result + newresult[0],
                                                      map(list, rv),
                                                      start=tic))
            return d
        else:
            return result + newresult[0]

    def _create_ids(self, subid, obj):
        """Create any missing streamids from a Timeseries object.
        This way a select will always return the right results.
        """
        uuids = []
        query = "SELECT "
        for ts in obj.itervalues():
            uuids.append("add_stream(%i, %s)" % (subid,
                                                 escape_string(ts['uuid'])))
    
        query += ','.join(uuids)
        return self._run_create(uuids, [], [[]])

    def add(self, subid, obj):
        d = self._create_ids(subid, obj)
        # d.addCallback(lambda rv: self._get_ids(subid, obj))
        d.addCallback(lambda rv: self._add_data(subid, rv, obj))

        # all the errbacks should propagate up to the request handler so we can return a 500
        return d

def del_streams(streams):
    try:
        r = rdb_pool.get()
        for sid in streams:
            settings.rdb.db_del(r, sid, 0, 0xffffffff)
    finally:
        rdb_pool.put(r)
        

class DataRequester:
    """Class to manage extracting data from the readingdb and return
    it either as a numpy matix, a list, or a smap object with
    readings.
    """
    def __init__(self, ndarray=False, as_smapobj=True, do_pandas=True):
        """
:param ndarray: return the data as a numpy matrix
:param: as_smapobj: return the data as a list of sMAP objects (dicts,
     with uuid and Readings set) instead of a bare list of data.
        """
        self.ndarray = ndarray
        self.as_smapobj = as_smapobj
        self.do_pandas = do_pandas

    def load_data(self, request, method, streaminfos):
        """
:param: request: a twisted http request
:param method: 'data', 'prev', or 'next'
:param streaminfos: a list of (uuid, streamid, timeunit, timezone) tuples data is requested for.
        """
        self.streaminfos = streaminfos
        ids = map(operator.itemgetter(1), streaminfos)
        units = map(operator.itemgetter(2), streaminfos)
        timezones = map(operator.itemgetter(3), streaminfos)
        pointwidth = request.args.get("pw", ["-1"])[0]

        pointwidth = int(pointwidth)
        # TODO be more elegant than bailing out if the time units for different streams are not the same
        # My guess is that we merge them by creating multiple rdb queries and joining them later...
        
        assert all(map(lambda x: x==units[0], units)), "Time units in multiple stream query not the same"
        stream_unit = units[0].lower()
        query_unit = request.args.get('unit',["ms"])[0]
        
        unit_defs = {"ns":1000000000.,"us":1000000.,"ms":1000.,"s":1}
        
        now = time.time()
        starttime = request.args.get('starttime', [None])[0]
        endtime = request.args.get('endtime',[None])[0]
        if starttime is None:
            starttime = int((now - 3600 * 24) * unit_defs[stream_unit])
        else:
            starttime = int(int(starttime) * unit_defs[stream_unit] / unit_defs[query_unit])
            
        if endtime is None:
            endtime = int(now * unit_defs[stream_unit])
        else:
            endtime = int(int(endtime) * unit_defs[stream_unit] / unit_defs[query_unit])
                
        # args are a bit different for different requests
        if method == 'data':
            method = settings.rdb.db_query
            args = [
                ids,
                starttime,
                endtime
                ]
            kwargs = {
                'limit': int(request.args.get('limit', [10000000])[0])
                }
        else:
            if method == 'prev':
                method = settings.rdb.db_prev
            elif method == 'next':
                method = settings.rdb.db_next
            args = [
                ids,
                starttime
                ]
            kwargs = {
                'n': int(request.args.get('limit', [1])[0])
                }
                

        # run the request in a (twisted) thread.
        d = threads.deferToThread(method, *args, **kwargs)

        # modify the results to be the required unit
        # we use an integer multiplier because we cannot afford to lose the precision
        # in our integer timestamps. It would convert to double if we accidently
        # had a floating point multiplier
        # if the multiplier is less than one, we would have bigger problems though,
        # so we just bite the pillow
        multiplier = unit_defs[query_unit] / unit_defs[stream_unit]
        if multiplier >=1 : 
            multiplier = int(multiplier)
        else:
            print "WARNING! TIME ADAPTING MULTIPLIER < 1: BADNESS 9001"
            multiplier = 1

        to_ns_mult = int(unit_defs["ns"] / unit_defs[stream_unit])

        if pointwidth != -1 and query_unit != "ns":
            raise Exception("mipdb emulation only supported for nanosecond queries")

            #TODO this is a policy thing that needs discussion
            #assert False, "Bailing hard: unit multiplier is less than 1: %f" % multiplier
        d.addCallback(self.modify_units, streaminfos, multiplier, query_unit)
         
        # add the data munging if requested
        if not self.ndarray or self.as_smapobj:
            d.addCallback(self.screw_data, streaminfos, pointwidth)
        return d

    def modify_units(self, data, streaminfos, multiplier, unit):
        #We also refactor the array of u64's into dt64's.
        rv = []

        for streaminfo, streamdata in zip(streaminfos, data):
            #this should be zero alloc
            tz = streaminfo[3]
            np.multiply(streamdata[0], multiplier, streamdata[0])
            #This too. It encodes the unit into the stream, but no TZ info
            dt = streamdata[0] if self.as_smapobj else streamdata[0].view("datetime64[%s]"%unit)
            #This will get us units and TZ
            if self.do_pandas and not self.as_smapobj:
                s = pd.Series(streamdata[1],index=dt)
                #According to my tests, this is all zcopy (modifying the ndarray modifies the final result)
                s.index = s.index.tz_localize("UTC") #The ints were units since UTC epoch
                s.index = s.index.tz_convert(tz) #But our dates are not in that timezone
                df = pandas.DataFrame(s)
                rv.append(df)
            else:
                rv.append((dt,streamdata[1]))
        return rv
        
        #TODO: make sure there is an input sanitiser that just drops requests if the date range
        #cannot be stuck in a 63 bit number, as opposed to dying in the readingdb C module.
        #This probably exists on the live code
    def check_data(self, data):
        """Run a check to see if the the data we get back from
        readingdb is sensible."""
        for d in data:
            times = set(d[:, 0])
            assert len(times) == len(d[:, 0])
        return data

    def screw_data(self, data, streamids, pointwidth):
        #This will need a change
        rv = []
        print "Screwing data, repr: ", repr(data)
        for (uid, id, unit, tz), d in zip(streamids, data):
            if not self.ndarray:
                d = (d[0].tolist(), d[1].tolist())
            if self.as_smapobj:
                #Note that this matches current behaviour in that there
                #are no timezone changes to the data. The time is in units since the UTC epoch
                #we HAVE the tz if we decide to do something funny here later

                #print "SMAPRV len=%d" % len(d)
                #print "SMARRVO = ", repr(d)

                #This is a proof of concept and will kill performance
                #The point is that all of this is done once in the mipdb design
                dr = []
                dx = []
                if pointwidth > 0 and self.as_smapobj:
                    then = time.time()
                    tarr = d[0]
                    darr = d[1]

                    shift = pointwidth
                    # pointwidth is in ns
                    #if to_ns_mult != 1:
                    #    np.multiply(tarr, to_ns_mult, tarr)
                    idx = 0
                    last_time = ((tarr[idx]) >> shift) if len(tarr) > 0 else 0
                    window_dat = []
                    window_tm = []
                    print "TARLEN: ", len(tarr)
                    for idx in xrange(len(tarr)):
                        if (tarr[idx]) >> shift == last_time:
                            window_dat.append(darr[idx])
                            window_tm.append(tarr[idx])
                            if idx != len(tarr) - 1:
                                continue

                        t = (last_time << shift)
                        if shift > 1: t += 1<<(shift-1) #Add half the time window
                        #end of a window
                        wobj = [t / 1000000, t % 1000000, #time
                                np.min(window_dat),
                                np.percentile(window_dat,25),
                                np.median(window_dat),
                                np.percentile(window_dat,75),
                                np.max(window_dat),
                                len(window_dat)]
                        dr.append([last_time << shift, np.mean(window_dat)])
                        dx.append(wobj)
                        window_dat = [darr[idx]]
                        window_tm = [tarr[idx]]
                        last_time = (tarr[idx]) >> shift

                    rv.append({'uuid': uid,
                               'Readings': dr,
                               'XReadings': dx})
                elif self.as_smapobj:
                    rv.append({'uuid': uid,
                               'Readings': zip(d[0],d[1])})
            else:
                rv.append(d)
        return rv


def send_result((request, result)):
    request.write(json.dumps(result))
    request.finish()

def log_time(result, start):
    logging.getLogger('stats').info("data load took %0.6fs" % (time.time() - start))
    return result

def data_load_result(request, method, result, send=False, **loadargs):
    """Callback that can be chained onto a db query to load the data
    from the reading db.

:param request: the twisted request
:param str method: data method
:param result: the result of a previous query (usually).  list of
    (uuid, streamid) tuples.
:param bool send: write the output to the request object.  If true,
    the resulting data will be sent back to the client by calling the
    request.write method.  Otherwise you can chain your own callback
    onto this guy.
:param loadargs: additional args for the data loader (see :py:class:`DataRequester`).
:return: a deferred which will fire with the result of loading the requested data.
    """
    count = int(request.args.get('streamlimit', ['1000'])[0])
    if count == 0:
        count = len(result)
    if len(result) > 0:
        loader = DataRequester(**loadargs)
        d = loader.load_data(request, method, result[:count])
        d.addCallback(log_time, time.time())
        return d
    else:
        return defer.succeed([])

