How to set up MatlabSmap.

(1) Unzip MatlabSmap.zip folder and extract from the java folder:
JavaSmap_0.1.jar

and from the java/lib folder:
javax.json-1.0-b06.jar

(2) Add jar paths to Matlab java path.

(Option 1) Add dynamically. For example, include in your m-file the following code:
javaaddpath('/Users/skoehler/Documents/SDB_MPC/smap/Matlab/JavaSmap_0.1.jar')
javaaddpath('/Users/skoehler/Documents/SDB_MPC/smap/java/JavaSmap/lib/javax.json-1.0-b06.jar')
javaaddpath('/Users/skoehler/Documents/SDB_MPC/smap/java/JavaSmap/lib/json-simple-1.1.1.jar')

(Option 2) To add statically, find and edit the following file to include those three jar paths.
$MATLAB\toolbox\local\classpath.txt

For more information, see: http://www.mathworks.com/matlabcentral/answers/96993-how-can-i-use-a-java-class-from-a-jar-file-in-matlab

Check java path by typing:
>> javaclasspath

(3) Download JSONLab. Add to Matlab path (e.g. use Set Path button or addpath())
http://www.mathworks.com/matlabcentral/fileexchange/33381-jsonlab--a-toolbox-to-encode-decode-json-files-in-matlab-octave

For a good review of using JSON in Matlab, see:
http://undocumentedmatlab.com/blog/json-matlab-integration


