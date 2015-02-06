function timearray = UTCtoPST(timearray)
% Take UTC time from Java and turns into PST time in Matlab.

timearray = timearray/86400/1000 + datenum(1970,1,1) - 1/24*8;