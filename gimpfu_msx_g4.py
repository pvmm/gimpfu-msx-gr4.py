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
from math import sqrt

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
FIXED_DITHERING = 3

PLUGIN_MSG = """Export bitmaps in MSX2 GRAPHICS 4 format (a.k.a. SCREEN 5 in BASIC)"""

tuple_key = lambda pair: pair[0]
tuple_value = lambda pair: pair[1]


def write_gr4(image, layer, filename, folder, dithering, exp_pal, image_enc):
    '''
    Export image to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param dithering: whether dithering is active
    @param exp_pal: export palette data too
    @param image_enc: output encoding
    '''
    
    errors = []
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
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

    buffer = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT

    # Create temporary image
    new_image = gimpfu.pdb.gimp_image_duplicate(image)
    histogram = create_histogram(new_image)
    palette = quantize_colors(histogram, MAX_COLORS)
    drawable = reduce_colors(image, palette, dithering)

    if exp_pal:
        pal9bits = [0] * (2 * MAX_COLORS)

        for color, index in palette:
            r, g, b = color
            pal9bits[index * 2] = 16 * r + b
            pal9bits[index * 2 + 1] = g

        encoded = struct.pack('<BHHH{}B'.format(len(pal9bits)), BIN_PREFIX, PALETTE_OFFSET,
                PALETTE_OFFSET + len(pal9bits), 0, *pal9bits[0:len(pal9bits)])
        file = open(os.path.join(folder, '%s.PAL' % filename), "wb")
        file.write(encoded)
        file.close()

    gimpfu.pdb.gimp_progress_init('Exporting image to %s format...' % image_enc, None)
    gimpfu.pdb.gimp_progress_update(0)

    buffer = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT
    step = 1.0 / height
    percent = 0.0
    palette = {k:v for k, v in palette}

    for y in range(0, height):
        for x in range(0, width):
            _, (color) = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            indexed = palette[color]
            pos = x // 2 + y * 128
            buffer[pos] |= indexed if x % 2 else indexed << 4;

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    # Discard temporary image
    gimpfu.pdb.gimp_image_delete(new_image)

    if image_enc == 'bin':
        encoded = struct.pack('<BHHH{}B'.format(len(buffer)), BIN_PREFIX, 0, len(buffer), 0, *buffer)
    else:
        return

    file = open(os.path.join(folder, '%s.SC5' % filename), "wb")
    file.write(encoded)
    file.close()


def create_histogram(image):
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)
    histogram = {}

    percent = 0.0
    step = 1.0 / height

    gimpfu.pdb.gimp_progress_init('Creating histogram...', None)
    gimpfu.pdb.gimp_progress_update(0)

    for y in range(height):

        for x in range(width):
            _, (r, g, b) = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            r = round(float(r) / 0xff * 7)
            g = round(float(g) / 0xff * 7)
            b = round(float(b) / 0xff * 7)
            histogram[(r, g, b)] = histogram.get((r, g, b), 0) + 1

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    return histogram.items()


def distance(src, dst):
    r1, g1, b1 = src
    r2, g2, b2 = dst

    return sqrt(
        (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
    )


def quantize_colors(histogram, length):
    palette = []

    gimpfu.pdb.gimp_progress_init('Quantizing colors...', None)
    gimpfu.pdb.gimp_progress_update(0.0)

    percent = 0.0
    step = float(len(histogram) - length) / 100

    while len(histogram) > length:
        # Order by number of pixels of the same color
        histogram.sort(key=tuple_value)

        color1, freq = histogram.pop(0)
        distances = [
            (idx, distance(color1, color2)) for idx, (color2, _) in enumerate(histogram[1:], start=1)
        ]

        index, _ = min(distances, key=tuple_value)
        # add first least frequent item into nearest element
        histogram[index] = (histogram[index][0], histogram[index][1] + freq)

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    for index, (color, _) in enumerate(sorted(histogram, key=tuple_key)):
        palette.append((color, index))

    return palette


def scatter_noise(drawable, x, y, error):
    NEIGHBORS = ( (+1, 0, 7.0/16), (-1, +1, 3.0/16), (-1, +1, 5.0/16), (+1, +1, 1.0/16) )

    for offset_x, offset_y, debt in NEIGHBORS:
        try:
            off_x, off_y = x + offset_x, y + offset_y

            if off_x < 0 or off_y < 0:
                continue

            nchannels, pixel = gimpfu.pdb.gimp_drawable_get_pixel(drawable, off_x, off_y)
            npixel = tuple(min(255, color + error * debt) for color, error in zip(pixel, error))
            gimpfu.pdb.gimp_drawable_set_pixel(drawable, off_x, off_y, nchannels, npixel)

        except Exception:
            pass # hey, gimp developers, Python 2.7 sucks!


def reduce_colors(image, palette, dithering=True):
    def nearest_index(pixel):
        return min([(idx, distance(pixel, color)) for color, idx in palette], key=tuple_value)[0]
 
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)
 
    gimpfu.pdb.gimp_progress_init('Downsampling...', None)
    gimpfu.pdb.gimp_progress_update(0.0)

    percent = 0.0
    step = 1.0 / height

    for y in range(height):
        for x in range(width):
            nchannels, (r1, g1, b1) = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            r2 = r1 & 0xe0; nr = round(float(r1) / 0xff * 7)
            g2 = g1 & 0xe0; ng = round(float(g1) / 0xff * 7)
            b2 = b1 & 0xe0; nb = round(float(b1) / 0xff * 7)
            index = nearest_index((nr, ng, nb))
            gimpfu.pdb.gimp_drawable_set_pixel(drawable, x, y, nchannels, palette[index][0])

            if dithering:
                error = [old - new for old, new in zip((r1, g1, b1), (r2, g2, b2))]
                scatter_noise(drawable, x, y, error)

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    return drawable




gimpfu.register("msx_gr4_exporter",
                PLUGIN_MSG,
                "Export MSX-compatible image", 
                "Pedro de Medeiros", "Pedro de Medeiros", "2021", 
                "<Image>/Filters/MSX/Export GRAPHICS 4 bitmap...", 
                "RGB*", [
                    (gimpfu.PF_STRING, "filename", "File name", DEFAULT_FILENAME),
                    (gimpfu.PF_DIRNAME, "folder", "Output Folder", DEFAULT_OUTPUT_DIR),
                    (gimpfu.PF_BOOL, "dithering", "Dithering", True),
                    #(gimpfu.PF_BOOL, "force-0black", "Force black as color 0", False),
                    (gimpfu.PF_BOOL, "exp-pal", "Export palette", True),
                    (gimpfu.PF_RADIO, "image-enc", "Image Encoding", DEFAULT_OUTPUT_FMT, (("BIN", "bin"),))
                ], 
                [], 
                write_gr4)

gimpfu.main()
