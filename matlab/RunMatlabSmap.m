%% RunMatlabSmap.m
% Use JavaSmap to collect data from sMap.

close all
clear all
clear java

disp('Please verify that you set the correct paths in MatlabSmap.m at line 11, 15, 16 and 17')

% jsonlab dynamic link if not set statically:
% addpath('C:\Users\Robin\Documents\MATLAB\DownloadedFun\JsonLab\jsonlab') 
% If commented, check the following paths are statically added to
% classpath.txt. Otherwise, uncomment and dynamically add jars to Java
% path. Check path with javaclasspath.
% javaaddpath('C:\Users\Robin\Desktop\java\JavaSmap_0.1.jar')
% javaaddpath('C:\Users\Robin\Desktop\java\lib\javax.json-1.0-b06.jar')
% javaaddpath('C:\Users\Robin\Desktop\java\lib\json-simple-1.1.1.jar')
%javaclasspath

%% Query Set Up
archiverUrl = 'http://new.openbms.org/backend/api/query';
js = JavaSmap(archiverUrl);
uuid_list = java.util.ArrayList;

%% Query Time
% % Option 1: choose a time frame that ends at the current time
% Helpful units in Java time
one_minute = 1000*60;
one_hour = one_minute*60;
one_day = 24*one_hour;
one_week = 7*one_day;
% endtime = java.lang.System.currentTimeMillis();
% start = endtime - one_day;

% Option 2: Choose specific dates
start = PSTtoUTC(datenum(2014,12,3,0,0,0));
endtime = PSTtoUTC(datenum(2014,12,4,0,0,0));

%% Specify your uuid here (you can find the uuid at http://new.openbms.org/plot/)
my_uuid = '395005af-a42c-587f-9c46-860f3061ef0d';
uuid_list.add(my_uuid);
result = js.data_uuid(uuid_list, start, endtime, int64(100000), int64(size(uuid_list)));

%% Postprocess
java.lang.System.out.println(result)
data = loadjson(char(result.toString()));
timestamp = data{1,1}.Readings(:,1);
% Convert from Java (UNIX) time to Matlab time, and from UTC to PST
timestamp = timestamp/86400/1000 + datenum(1970,1,1) - 1/24*8;
datastamp = data{1}.Readings(:,2);

%% Plot
plot(timestamp,(datastamp-32)*5/9)
datetick('x')