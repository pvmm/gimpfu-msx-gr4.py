# gimpfu-msx-gr4.py

![Options dialog](images/dialog.jpg "Options dialog")

GIMP script to export bitmap as GRAPHICS 4 file (aka SCREEN 5). GRAPHICS 4 specs are: 

* 256x212 (or 256x192);
* 16 color palette (from 512);
* 4 pages;
* sprite mode 2;

## Installation: 
- Put this file into your GIMP plugin directory, i.e. `~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/gimpfu_msx_g4.py`
- Restart GIMP
- Run script via Filters/MSX/Export GRAPHICS 4 bitmap...

## Loading bin (.SC5) files

You may load files created by this plug-in using this simple code in BASIC:
```
10 SCREEN 5
20 BLOAD"NONAME.SC5",S
30 GOTO 30
```

## TODO

* make it faster;
* palette export;
* RLE encoding;
* aPLib compression;
* converting layers into pages;
