#!/usr/bin/env python
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
DEFAULT_OUTPUT_FMT = 'SC5'
MAX_COLORS = 16
MAX_WIDTH = 256
MAX_HEIGHT = 256
MAX_DAT_HEIGHT = 212
MAX_PAGES = 4
PALETTE_OFFSET = 0x7680
FIXED_DITHERING = 3

# Base image type
RGB = 0
GRAY = 1
INDEXED = 2

PLUGIN_MSG = """Export bitmaps in MSX2 GRAPHICS 4 format (a.k.a. SCREEN 5 in BASIC)"""

tuple_key = lambda pair: pair[0]
tuple_value = lambda pair: pair[1]


def create_distance_query(palette):
    palmap = {k:v for k, v in palette}

    def query_index(pixel):
        idx = palmap.get(pixel)
        if idx:
            return palette[idx][1], palette[idx][0]
        dsts = [(idx, distance(pixel, color), color) for color, idx in palette]
        mdst = min(dsts, key=tuple_value)
        #print 'pixel =', pixel, ' distances =', dsts, ' min =', mdst
        palmap[pixel] = mdst[0]
        return mdst[0], mdst[2]

    return query_index


def write_gr4(image, layer, filename, folder, dithering, exp_pal, transparency, image_enc, exp_ptp):
    '''
    Export image to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param dithering: whether dithering is active
    @param exp_pal: export palette data too
    @param transparency: transparency consumes one color index
    @param image_enc: output encoding
    @param exp_ptp: export plain-text-palette data too
    '''

    filename = filename.upper()
    errors = []

    drawable = gimpfu.pdb.gimp_image_active_drawable(image)

    # In screen 5 only even sizes are permitted.
    width, height = gimpfu.pdb.gimp_drawable_width(drawable) & ~1, gimpfu.pdb.gimp_drawable_height(drawable) & ~1

    if image_enc != 'no-output':
        if os.path.exists(os.path.join(folder, '%s.%s' % (filename, image_enc))):
            errors.append('Output file "%s.%s" already exists.' % (filename, image_enc))

        if image_enc == 'DAT':
            if width > MAX_WIDTH:
                errors.append('Drawable width must be less than or equal to %i.' % MAX_WIDTH)
            if height > MAX_DAT_HEIGHT:
                errors.append('Drawable height must be less than or equal to %i.' % MAX_DAT_HEIGHT)

        elif width != MAX_WIDTH:
            errors.append('Drawable width must be %i.' % MAX_WIDTH)

    if exp_pal and os.path.exists(os.path.join(folder, '%s.PAL' % filename)):
        errors.append('Output palette "%s.PAL" file already exists.' % filename)

    if exp_ptp and os.path.exists(os.path.join(folder, '%s.TXT' % filename)):
        errors.append('Output plain text palette "%s.TXT" file already exists.' % filename)

    if height > MAX_HEIGHT * MAX_PAGES:
        errors.append('Drawable height must not be bigger than %i.' % (MAX_HEIGHT * MAX_PAGES))

    if image_enc in ('RLE', 'aPLib'):
        errors.append("compression is not implemented yet.")

    if errors:
        gimp.message("\n".join(errors))
        return

    # Create temporary image
    new_image = gimpfu.pdb.gimp_image_duplicate(image)

    # Check if image is indexed and convert to RGB.
    palette = []
    type_ = gimpfu.pdb.gimp_image_base_type(new_image);
    if type_ == INDEXED:
        num_bytes, colormap = gimpfu.pdb.gimp_image_get_colormap(new_image)
        # Convert to RGB to reduce color count. Old palette is discarded.
        if num_bytes // 3 > MAX_COLORS or dithering:
            type_ = RGB
            gimpfu.pdb.gimp_image_convert_rgb(new_image);
        else:
            # Convert colormap into palette.
            transparency = 0
            for i, j in enumerate(range(0, num_bytes, 3)):
                palette.append(((colormap[j], colormap[j + 1], colormap[j + 2]), i))
    elif type_ == GRAY:
        type_ = RGB
        gimpfu.pdb.gimp_image_convert_rgb(new_image);

    # create palette data
    pal9bits = [0] * 2 * MAX_COLORS
    txtpal = [(0, 0, 0)] * MAX_COLORS

    if not palette:
        drawable = downsampling(new_image, dithering)
        histogram = create_histogram(drawable)
        palette = quantize_colors(histogram, MAX_COLORS - transparency)
        query = create_distance_query(palette)

    for (r, g, b), index in palette:
        # Start palette at color 1 if transparency is set.
        pal9bits[(index + transparency) * 2] = 16 * (r >> 5) + (b >> 5)
        pal9bits[(index + transparency) * 2 + 1] = (g >> 5)
        txtpal[(index + transparency)] = (r >> 5, g >> 5, b >> 5)

    if exp_ptp:
        file = open(os.path.join(folder, '%s.TXT' % filename), 'wt')
        print >>file, 'SCREEN 5 palette:'
        i = 0
        for (r, g, b) in txtpal:
            print >>file, '%i: %i, %i, %i' % (i, r, g, b)
            i += 1
        file.close()

    if exp_pal:
        encoded = struct.pack('<BHHH{}B'.format(len(pal9bits)), BIN_PREFIX, PALETTE_OFFSET,
                PALETTE_OFFSET + len(pal9bits), 0, *pal9bits[0:len(pal9bits)])
        file = open(os.path.join(folder, '%s.PAL' % filename), 'wb')
        file.write(encoded)
        file.close()

    gimpfu.pdb.gimp_progress_init('Exporting image to %s format...' % image_enc, None)
    gimpfu.pdb.gimp_progress_update(0)

    # buffer = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT
    if image_enc == 'DAT':
        buffer = [0] * (width // 2) * height
    else:
        buffer = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT

    step = 1.0 / height
    percent = 0.0

    if image_enc != 'no-output':
        for y in range(0, height):
            for x in range(0, width):
                if type_ == RGB:
                    # num_channels, RGBA
                    _, c = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
                    index, _ = query((c[0], c[1], c[2]))
                else:
                    # num_channels, index, alpha
                    _, (index, _) = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
                pos = x // 2 + y * (width // 2)
                buffer[pos] |= (index + transparency) if x % 2 else (index + transparency) << 4;

            percent += step
            gimpfu.pdb.gimp_progress_update(percent)

        # Embed palette into image data (SC5 only)
        if image_enc == 'SC5':
            for pos in range(32):
                buffer[0x7680 + pos] = pal9bits[pos]

        if image_enc == 'RAW':
            encoded = struct.pack('<{}B'.format(len(buffer)), *buffer)
        elif image_enc == 'DAT':
            encoded = struct.pack('<HH{}B'.format(width * height // 2), width, height, *buffer)
        else:
            encoded = struct.pack('<BHHH{}B'.format(len(buffer)), BIN_PREFIX, 0, len(buffer), 0, *buffer)
        file = open(os.path.join(folder, '%s.%s' % (filename, image_enc)), 'wb')
        file.write(encoded)
        file.close()
    else:
        gimpfu.pdb.gimp_display_new(new_image)


def create_histogram(drawable):
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)
    histogram = {}

    percent = 0.0
    step = 1.0 / height

    gimpfu.pdb.gimp_progress_init('Creating histogram...', None)
    gimpfu.pdb.gimp_progress_update(0)

    for y in range(height):
        for x in range(width):
            _, c = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            histogram[(c[0], c[1], c[2])] = histogram.get((c[0], c[1], c[2]), 0) + 1

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
    """Group similar colours reducing palette to "length"."""
    palette = []

    gimpfu.pdb.gimp_progress_init('Quantizing colors...', None)
    gimpfu.pdb.gimp_progress_update(0.0)

    percent = 0.0
    step = float(len(histogram) - length) / 100

    while len(histogram) > length:
        # Order histogram by color usage (this is slooooooow!)
        histogram.sort(key=tuple_value)

        # Get least frequent item and its nearest cousin by colour
        color1, freq = histogram.pop(0)
        distances = [
            (idx, distance(color1, color2)) for idx, (color2, _) in enumerate(histogram[1:], start=1)
        ]
        index, _ = min(distances, key=tuple_value)

        # add removed item's frequency into nearest cousin's frequency
        histogram[index] = (histogram[index][0], histogram[index][1] + freq)

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    for index, (color, _) in enumerate(sorted(histogram, key=tuple_key)):
        palette.append((color, index))

    return palette


def scatter_noise(drawable, x, y, error):
    NEIGHBORS = ( (+1, 0, 7.0/16), (-1, +1, 3.0/16), (-1, +1, 5.0/16), (+1, +1, 1.0/16) )
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)

    for offset_x, offset_y, debt in NEIGHBORS:
        try:
            off_x, off_y = x + offset_x, y + offset_y

            if off_x < 0 or off_y < 0 or off_x >= width or off_y >= height:
                continue

            nchannels, pixel = gimpfu.pdb.gimp_drawable_get_pixel(drawable, off_x, off_y)
            npixel = tuple(max(0, min(255, round(color + error * debt))) for color, error in zip(pixel, error))
            #print 'pos:', (off_x + 255 * off_y), " pixel/npixel:", pixel, npixel
            gimpfu.pdb.gimp_drawable_set_pixel(drawable, off_x, off_y, nchannels, npixel)

        except Exception:
            pass # hey, gimp developers, Python 2.7 sucks!


def downsampling(image, dithering=True):
    """Reduction to 9-bit palette with optional dithering."""
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
    # Only even sizes are permitted.
    width, height = gimpfu.pdb.gimp_drawable_width(drawable) & ~1, gimpfu.pdb.gimp_drawable_height(drawable) & ~1
 
    gimpfu.pdb.gimp_progress_init('Downsampling%s...' % (' with dithering (slow!)' if dithering else ''), None)
    gimpfu.pdb.gimp_progress_update(0.0)

    percent = 0.0
    step = 1.0 / height

    for y in range(height):
        for x in range(width):
            nchannels, c = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            r = int(round(float(c[0]) / 0xff * 7)) << 5
            g = int(round(float(c[1]) / 0xff * 7)) << 5
            b = int(round(float(c[2]) / 0xff * 7)) << 5
            gimpfu.pdb.gimp_drawable_set_pixel(drawable, x, y, nchannels, (r, g, b))

            if dithering:
                error = [old - new for old, new in zip((c[0], c[1], c[2]), (r, g, b))]
                scatter_noise(drawable, x, y, error)

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    return drawable


gimpfu.register("msx_gr4_exporter",
                PLUGIN_MSG,
                "Export MSX-compatible image", 
                "Pedro de Medeiros", "Pedro de Medeiros", "2021-2023", 
                "<Image>/Filters/MSX/Export GRAPHICS 4 bitmap...", 
                "*", [
                    (gimpfu.PF_STRING, "filename", "File name", DEFAULT_FILENAME),
                    (gimpfu.PF_DIRNAME, "folder", "Output Folder", DEFAULT_OUTPUT_DIR),
                    (gimpfu.PF_BOOL, "dithering", "Dithering", True),
                    (gimpfu.PF_BOOL, "exp-pal", "Export palette", False),
                    (gimpfu.PF_BOOL, "transparency", "Enable transparency", True),
                    (gimpfu.PF_RADIO, "image-enc", "Image Encoding", DEFAULT_OUTPUT_FMT,
                       (("Binary format with palette (SC5)", "SC5"),
                        ("Binary format without palette (SR5)", "SR5"),
                        ("MSX-BASIC COPY to disk (no palette)", "DAT"),
                        ("Raw file (no palette)", "RAW"),
                        ("No output (image in new window)", "no-output"))),
                    (gimpfu.PF_BOOL, "exp-ptp", "Export plain text palette", False)
                ], 
                [], 
                write_gr4)

gimpfu.main()
