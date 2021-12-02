#! /usr/bin/env python
'''
Created on 2021/11/30

@author: Pedro de Medeiros <pedro.medeiros@gmail.com>

Installation: 
    - Put this file into your GIMP plugin directory, i.e. ~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/gimpfu_msx_g4.py
    - Restart Gimp
    - Run script via Filters/MSX/Export GRAPHICS 4 bitmap...
'''

import gimpfu
import gimp
import os
import struct

BIN_PREFIX = 0xFE
DEFAULT_FILENAME = 'NONAME'
DEFAULT_VRES = 'v212'
DEFAULT_OUTPUT_DIR = os.getcwd()
DEFAULT_OUTPUT_FMT = 'bin'
MAX_COLORS = 16
MAX_WIDTH = 256
MAX_HEIGHT = 212
MAX_PAGES = 4
PALETTE_OFFSET = 0x7680

def write_gr4(img, layer, filename, folder, black0, exp_pal, image_enc):
    '''
    Export image to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param black0: force black as color 0
    @param exp_pal: export palette data too
    @param image_enc: output encoding
    '''
    
    errors = []
    drawable = gimpfu.pdb.gimp_image_active_drawable(img)

    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)

    if width != MAX_WIDTH:
        errors.append('Drawable width must be %i.' % MAX_WIDTH)

    if height > MAX_HEIGHT * MAX_PAGES:
        errors.append('Drawable height must not be bigger than %i.' % (MAX_HEIGHT * MAX_PAGES))

    if image_enc != 'bin':
        errors.append("RLE and aPLib encoding are not implemented yet.")

    if errors:
        gimp.message("\n".join(errors))
        return

    # Saving palette
    cfq, crep = reduce_colors(img)

    if len(cfq) > MAX_COLORS:
        errors.append('Image must not have more than 16 colors.')

    gimpfu.pdb.gimp_progress_init('Converting to %i colors...' % len(cfq), None)
    gimpfu.pdb.gimp_progress_update(0)

    palette = [0] * (2 * MAX_COLORS)

    for color, index in cfq.items():
        r, g, b = color
        palette[index * 2] = 16 * r + b
        palette[index * 2 + 1] = g
        print("palette[%i] = (%i, %i, %i)" % (index, r, g, b))

    if exp_pal:
        encoded = struct.pack('<BHHH{}B'.format(len(palette)), BIN_PREFIX, PALETTE_OFFSET,
                PALETTE_OFFSET + len(palette), 0, *palette[0:len(palette)])
        file = open(os.path.join(folder, '%s.PAL' % filename), "wb")
        file.write(encoded)
        file.close()

    pixels = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT
    step = 1.0 / height
    percent = 0.0
    printed = {}

    for y in range(0, height):
        for x in range(0, width):
            _, (indexed,) = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)

            if printed.get(indexed) == None:
                print("* replaced %i by %i" % (indexed, crep[indexed]))
                printed[indexed] = True

            indexed = crep[indexed]
            pos = x // 2 + y * 128
            pixels[pos] |= indexed if x % 2 else indexed << 4;

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    if image_enc == 'bin':
        encoded = struct.pack('<BHHH{}B'.format(len(pixels)), BIN_PREFIX, 0, len(pixels), 0, *pixels)
    else:
        return

    file = open(os.path.join(folder, '%s.SC5' % filename), "wb")
    file.write(encoded)
    file.close()


def reduce_colors(img):
    num_bytes, cmap = gimpfu.pdb.gimp_image_get_cmap(img)
    cfq = {}
    crep = {}

    for i in range(0, num_bytes, 3):
        print("%i: checking color %i..." % (i, i // 3))
        r = round(float(cmap[i]) / 0xff * 7) # // 32
        g = round(float(cmap[i + 1]) / 0xff * 7) # // 32
        b = round(float(cmap[i + 2]) / 0xff * 7) # // 32

        if cfq.get((r, g, b)) == None:
            cfq[(r, g, b)] = len(cfq)

        crep[i // 3] = cfq[(r, g, b)]
        print("* replacing index %i (#%02x%02x%02x) by %i (#%x%x%x)" % (i // 3, cmap[i], cmap[i + 1],
            cmap[i + 2], cfq[(r, g, b)], r, g, b))

    return cfq, crep


gimpfu.register("msx_gr4_exporter",
                "Export bitmaps in GRAPHICS 4 format (MSX2)", 
                "Export MSX-compatible image", 
                "Pedro de Medeiros", "Pedro de Medeiros", "2021", 
                "<Image>/Filters/MSX/Export GRAPHICS 4 bitmap...", 
                "INDEXED", [
                    (gimpfu.PF_STRING, "filename", "File name", DEFAULT_FILENAME),
                    (gimpfu.PF_DIRNAME, "folder", "Output Folder", DEFAULT_OUTPUT_DIR),
                    #(gimpfu.PF_BOOL, "layers2pages", "Convert layers into pages", False),
                    (gimpfu.PF_BOOL, "force_0_black", "Force black as color 0", False),
                    (gimpfu.PF_BOOL, "exp_pal", "Export palette", True),
                    (gimpfu.PF_RADIO, "image-enc", "Image Encoding", DEFAULT_OUTPUT_FMT, (("BIN", "bin"),
                        ("RLE", "rle"), ("aPLib", "aplib"))),
                ], 
                [], 
                write_gr4)

gimpfu.main()
