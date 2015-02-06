function timearray = PSTtoUTC(timearray)
% Take PST time in Matlab to UTC time in Java.

timearray = (timearray +1/24*8 - datenum(1970,1,1))*86400*1000;
