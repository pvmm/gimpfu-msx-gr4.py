# gimpfu-msx-gr4.py

![Options dialog](images/dialog.png "Options dialog")

GIMP script to export bitmap as GRAPHICS 4 file (a.k.a. "SCREEN 5"). GRAPHICS 4 specs are: 

* 256x256 page with a 256x212 or 256x192 viewport;
* 16 color palette (from 512);
* 4 pages;
* sprite mode 2;

Plug-in is accessible through _Filters > MSX >> Export GRAPHICS 4 bitmap_.  You may disable **Image Encoding** altogether to create an image inside GIMP and not export it to disk at all. In this case, the plug-in doesn't check image size. But be warned: big images tend to take a very. Long. Time.

## Original vs sample image

### As usual, here is a picture of a nice girl for comparison:
![Original image](images/original.jpg "Original image")
![Sample image](images/sample.jpg "Sample image")

## Installation: 
- Put the source file (`gimpfu_msx_g.py`) into your GIMP plugin directory:
  - if you installed GIMP as a normal package, it's `~/.config/GIMP/2.10/plug-ins/`;
  - if you installed GIMP as a flatpak package, it's `~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/`;
- Restart GIMP

## Loading binary (.SC5) files

You may load files created by this plug-in using this simple code in BASIC:
```
10 SCREEN 5
15 REM use line below if transparency is disabled
20 VDP(9)=VDP(9) OR &H20
30 BLOAD"NONAME.SC5",S
40 BLOAD"NONAME.PAL",S
50 COLOR=RESTORE
60 IF INKEY$ = "" GOTO 60
```
First file (NONAME.SC5) is the pattern data and second (NONAME.PAL) is the palette.

## TODO

* ordered dithering;
* make it faster;
* ~~enable or disable transparent colour~~
* ~~palette export;~~
* ~~RGB to indexed conversion;~~
* ~~export raw file to be used by external compressors~~
* ~~ignore alpha channel instead of triggering errors~~
* RLE encoding;
* aPLib compression;
* converting layers into pages;
