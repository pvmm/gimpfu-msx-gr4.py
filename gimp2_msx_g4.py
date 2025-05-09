#!/usr/bin/env python
'''
Created on 2021/11/30

@author: Pedro de Medeiros <pedro.medeiros@gmail.com>

Installation: 
    - For GIMP2, put this file into your GIMP plugin directory, i.e. ~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/gimp2_msx_g4.py
    - For GIMP3, put this file inside a subdirectory with the same name in your plugin directory, i.e. ~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/gimp3_msx_g4/gimp3_msx_g4.py
    - Restart Gimp
    - Run script via Filters/MSX/Export GRAPHICS 4 bitmap...
'''

from __future__ import print_function
import gimpfu
import gimp
import os
import struct
import traceback
import sys
from pprint import pprint
from math import sqrt


# constants
NOTRANS = (None, None, None, None)
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


# base image type
RGB = 0
GRAY = 1
INDEXED = 2


# tuple functions
tuple_key = lambda pair: pair[0]
tuple_value = lambda pair: pair[1]


# error types
class InvalidAlphaValueError(Exception): pass

class ImageFormatError(Exception): pass

class NoTransparentColorError(Exception): pass


def create_distance_query(palette):
    """Create function that returns the nearest colour by value."""
    palmap = {k:v for k, v in palette}
    def query_index(pixel):
        pixel = pixel[0:3]  # remove alpha channel
        i = palmap.get(pixel)
        # Exact match returns (index and colour tuple)
        if i: return palette[i][1], palette[i][0]
        distances = [(i, distance(pixel, color), color) for color, i in palette]
        nearest = min(distances, key=tuple_value)
        #pprint(('pixel =', pixel, ' distances =', distances, ' min =', nearest))
        # Store value for fast lookup next time
        palmap[pixel] = nearest[0]
        # index and colour tuple
        return nearest[0], nearest[2]

    return query_index


class PluginConnector:
    """Connector object that interfaces with GIMP plugin."""
    def __init__(self, image):
        self.drawable = gimpfu.pdb.gimp_image_active_drawable(image)
        self.width = gimpfu.pdb.gimp_drawable_width(self.drawable)
        self.height = gimpfu.pdb.gimp_drawable_height(self.drawable)
        self.region = self.drawable.get_pixel_rgn(0, 0, self.width, self.height, True, False)
        # convert (r, g, b, a) string to list of tuples
        tmp = self.region[0:self.width, 0:self.height]
        self.buffer = [tuple([ord(x) for x in tmp[i : i + 4]]) for i in range(0, len(tmp), 4)]


    def get_pixel(self, x, y):
        return self.buffer[x + self.width * y]


    def set_pixel(self, x, y, pixel):
        # make sure pixel is a tuple of integers
        r, g, b = pixel[0:3]
        self.buffer[x + self.width * y] = (int(r), int(g), int(b), 255)


    def flush(self):
        # convert list of tuples back to string
        tmp = "".join([chr(r) + chr(g) + chr(b) + chr(a) for r, g, b, a in self.buffer])
        self.region[0:self.width, 0:self.height] = tmp


    def set_progress(self, fraction=0.0, text=""):
        gimpfu.pdb.gimp_progress_update(fraction)
        if text:
            gimpfu.pdb.gimp_progress_init(text, None)


def check_params(image, filename, folder, encoding, exp_pal, paltxt_file):
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)
    errors = []
    if encoding != 'no-output':
        if os.path.exists(os.path.join(folder, '%s.%s' % (filename, encoding))):
            errors.append('Output file "%s.%s" already exists.' % (filename, encoding))

        if encoding == 'DAT':
            if width > MAX_WIDTH:
                errors.append('Drawable width must be less than or equal to %i.' % MAX_WIDTH)
            if height > MAX_DAT_HEIGHT:
                errors.append('Drawable height must be less than or equal to %i.' % MAX_DAT_HEIGHT)

        elif width != MAX_WIDTH:
            errors.append('Drawable width must be %i.' % MAX_WIDTH)

    if exp_pal and os.path.exists(os.path.join(folder, '%s.PAL' % filename)):
        errors.append('Output palette "%s.PAL" file already exists.' % filename)

    if paltxt_file and os.path.exists(os.path.join(folder, '%s.TXT' % filename)):
        errors.append('Output plain text palette "%s.TXT" file already exists.' % filename)

    if height > MAX_HEIGHT * MAX_PAGES:
        errors.append('Drawable height must not be bigger than %i.' % (MAX_HEIGHT * MAX_PAGES))

    if encoding in ('RLE', 'aPLib'):
        errors.append('compression is not implemented yet.')

    return []


def do_write_g4(image, layer, filename, folder, dithering, exp_pal, skip_index0, trans_color, encoding, paltxt_file):
    errors = check_params(image, filename, folder, encoding, exp_pal, paltxt_file)
    if errors:
        gimp.message("\n".join(errors))
        return

    filename = filename.upper()

    # If there is no transparency, there is no predefined transparent color
    if not skip_index0 or not trans_color:
        trans_color = NOTRANS
    elif trans_color:
        # convert internal RGB pixel into tuple
        trans_color = tuple(trans_color)

    # Create temporary RGBA image with alpha channel
    new_image = gimpfu.pdb.gimp_image_duplicate(image)
    if not new_image.layers[0].has_alpha:
        new_image.layers[0].add_alpha()

    # Check if image is indexed or grayscale and convert to RGB.
    palette = []
    max_colors = MAX_COLORS - (1 if skip_index0 else 0)
    type_ = gimpfu.pdb.gimp_image_base_type(new_image);
    if type_ == INDEXED:
        num_bytes, colormap = gimpfu.pdb.gimp_image_get_colormap(new_image)
        # Convert to RGB to reduce color count. Old palette is discarded.
        if num_bytes // 3 > max_colors or dithering:
            palette = []
        gimpfu.pdb.gimp_image_convert_rgb(new_image);
    elif type_ == GRAY:
        gimpfu.pdb.gimp_image_convert_rgb(new_image);
    connector = PluginConnector(new_image)

    # create palette data
    pal9bits = [0] * 2 * MAX_COLORS
    txtpal = [(0, 0, 0)] * MAX_COLORS

    if not palette:
        use_transparency = fix_transparency(connector, trans_color)
        # disable dithering when transparency is used
        dithering = False if use_transparency else dithering
        downsampling(connector, trans_color, dithering)
        histogram = create_histogram(connector, trans_color)
        palette = quantize_colors(connector, histogram, max_colors)
        query = create_distance_query(palette)

    for (r, g, b), index in palette:
        # Start palette at color 1 if transparency is set.
        i = index + (1 if skip_index0 else 0)
        pal9bits[i * 2] = 16 * (r >> 5) + (b >> 5)
        pal9bits[i * 2 + 1] = (g >> 5)
        txtpal[i] = (r >> 5, g >> 5, b >> 5)

    if paltxt_file:
        file = open(os.path.join(folder, '%s.TXT' % filename), 'wt')
        print('SCREEN 5 palette:', file=file)
        i = 0
        for (r, g, b) in txtpal:
            print('%i: %i, %i, %i' % (i, r, g, b), file=file)
            i += 1
        file.close()

    if exp_pal:
        encoded = struct.pack('<BHHH{}B'.format(len(pal9bits)), BIN_PREFIX, PALETTE_OFFSET,
                PALETTE_OFFSET + len(pal9bits), 0, *pal9bits[0:len(pal9bits)])
        file = open(os.path.join(folder, '%s.PAL' % filename), 'wb')
        file.write(encoded)
        file.close()

    connector.set_progress(text="Exporting image to {} format".format(encoding))

    # buffer = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT
    if encoding == 'DAT':
        buffer = [0] * (connector.width // 2) * connector.height
    else:
        buffer = [0] * (MAX_WIDTH // 2) * MAX_HEIGHT

    if encoding != 'no-output':
        for y in range(0, connector.height):
            for x in range(0, connector.width):
                r, g, b, a = connector.get_pixel(x, y)
                if (r, g, b) == trans_color[0:3]:
                    # index of transparent color is always 0
                    index = 0
                else:
                    index, _ = query((r, g, b))
                    index += 1 if skip_index0 else 0
                pos = x // 2 + y * (connector.width // 2)
                buffer[pos] |= index if x % 2 else index << 4;
            connector.set_progress(float(y) / connector.height)

        # Embed palette into image data (SC5 only)
        if encoding == 'SC5':
            for pos in range(32):
                buffer[0x7680 + pos] = pal9bits[pos]
        if encoding == 'RAW':
            encoded = struct.pack('<{}B'.format(len(buffer)), *buffer)
        elif encoding == 'DAT':
            encoded = struct.pack('<HH{}B'.format(connector.width * connector.height // 2), connector.width, connector.height, *buffer)
        else:
            encoded = struct.pack('<BHHH{}B'.format(len(buffer)), BIN_PREFIX, 0, len(buffer), 0, *buffer)
        file = open(os.path.join(folder, '%s.%s' % (filename, encoding)), 'wb')
        file.write(encoded)
        file.close()
        gimp.delete(new_image)
    else:
        for y in range(0, connector.height):
            for x in range(0, connector.width):
                c = connector.get_pixel(x, y)
                _, d = query(c)
                connector.set_pixel(x, y, d)
            connector.set_progress(float(y) / connector.height)
        connector.flush()
        # create new image window
        gimpfu.pdb.gimp_display_new(new_image)


def fix_transparency(connector, trans_color):
    """Remove alpha channel and find if transparency is really used."""
    used_transparency = 0
    connector.set_progress(text="Searching alpha channel...")
    for y in range(connector.height):
        for x in range(connector.width):
            (r, g, b, a) = connector.get_pixel(x, y)
            if not a in (0, 255):
                raise InvalidAlphaValueError("Invalid alpha value {} (0 or 255 expected).".format(a))
            elif a == 0:
                if trans_color == NOTRANS:
                    raise NoTransparentColorError("Transparent pixel not expected but found in image.")
                # replace alpha channel set to 0 with trans_color
                connector.set_pixel(x, y, trans_color)
                used_transparency = 1
            elif (r, g, b) == trans_color[0:3]:
                used_transparency = 1
        connector.set_progress(float(y) / connector.height)
    return used_transparency


def create_histogram(connector, trans_color):
    histogram = {}
    connector.set_progress(text="Creating histogram...")
    for y in range(connector.height):
        for x in range(connector.width):
            r, g, b, a = connector.get_pixel(x, y)
            if (r, g, b) == trans_color[0:3]: continue
            histogram[(r, g, b)] = histogram.get((r, g, b), 0) + 1
        connector.set_progress(float(y) / connector.height)
    #pprint(("histogram: ", sorted(histogram.items(), key=tuple_value, reverse=True)[0:16]))
    return histogram.items()


def distance(src, dst):
    r1, g1, b1 = src
    r2, g2, b2 = dst
    return sqrt(
        (r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2
    )


def quantize_colors(connector, histogram, length):
    """Group similar colours reducing palette to "length"."""
    palette = []
    connector.set_progress(text="Quantizing colors...")
    hist = list(histogram)
    while len(hist) > length:
        # Order histogram by color usage
        hist.sort(key=tuple_value)

        # Replace least frequent item by its nearest cousin by colour distance
        color1, freq = hist.pop(0)
        distances = [
            (idx, distance(color1, color2)) for idx, (color2, ignored) in enumerate(hist[1:], start=1)
        ]
        nearest, ignored = min(distances, key=tuple_value)

        # add removed item's frequency into nearest cousin's frequency
        hist[nearest] = (hist[nearest][0], hist[nearest][1] + freq)
        connector.set_progress(len(hist) / float(length))

    for index, (color, ignored) in enumerate(sorted(hist, key=tuple_key)):
        palette.append((color, index))

    #pprint(("palette", palette))
    return palette


def compress_rgb(r, g, b, a=255):
    r = int(round(float(r) / 0xff * 7)) << 5
    g = int(round(float(g) / 0xff * 7)) << 5
    b = int(round(float(b) / 0xff * 7)) << 5
    return (r, g, b, 255)


def scatter_noise(connector, x, y, error):
    NEIGHBORS = ( (+1, 0, 7.0/16), (-1, +1, 3.0/16), (-1, +1, 5.0/16), (+1, +1, 1.0/16) )
    for offset_x, offset_y, debt in NEIGHBORS:
        off_x, off_y = x + offset_x, y + offset_y
        if off_x < 0 or off_y < 0 or off_x >= connector.width or off_y >= connector.height: continue
        pixel = connector.get_pixel(off_x, off_y)[0:3]
        npixel = tuple(max(0, min(255, round(color + error * debt))) for color, error in zip(pixel[0:3], error))
        #pprint(('pos:', (off_x, off_y), ':', pixel[0:3], "->", npixel))
        connector.set_pixel(off_x, off_y, npixel)


def downsampling(connector, trans_color, dithering):
    """Reduction to 9-bit palette with optional dithering."""
    connector.set_progress(text="Downsampling with dithering..." if dithering else "Downsampling...")
    for y in range(connector.height):
        for x in range(connector.width):
            r, g, b, a = connector.get_pixel(x, y)
            if (r, g, b) != trans_color[0:3]:
                d = compress_rgb(r, g, b)
                connector.set_pixel(x, y, d)
            if dithering:
                # ignore alpha channel in c and d
                error = [old - new for old, new in zip((r, g, b), d[0:3])]
                scatter_noise(connector, x, y, error)
        connector.set_progress(float(y) / connector.height)


def write_g4(image, layer, filename, folder, dithering, exp_pal, skip_index0, trans_color, encoding, paltxt_file):
    '''
    Export image to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param dithering: whether dithering is active
    @param exp_pal: export palette data too
    @param skip_index0: transparency support uses color index 0
    @param trans_color: RGB components of input color to be considered transparency
    @param encoding: output encoding
    @param paltxt_file: export plain-text-palette data too
    '''
    try:
        do_write_g4(image, layer, filename, folder, dithering, exp_pal, skip_index0, trans_color, encoding, paltxt_file)
    except Exception:
        gimp.message(traceback.format_exc())


def write_g4_alpha(image, layer, filename, folder, dithering, exp_pal, encoding, paltxt_file):
    '''
    Export image with alpha to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param dithering: whether dithering is active
    @param exp_pal: export palette data too
    @param encoding: output encoding
    @param paltxt_file: export plain-text-palette data too
    '''
    try:
        do_write_g4(image, layer, filename, folder, dithering, exp_pal, True, None, encoding, paltxt_file)
    except Exception:
        gimp.message(traceback.format_exc())


gimpfu.register("msx_g4_exporter",
                "Export RGB image to GRAPHICS 4 bitmap (a.k.a. SCREEN 5 in BASIC)",
                "Export MSX-compatible image",
                "Pedro de Medeiros", "Pedro de Medeiros", "2021-2025",
                "<Image>/Filters/MSX/Export GRAPHICS 4 bitmap...",
                "RGB*, INDEXED*, GRAY*", [
                    (gimpfu.PF_STRING, "filename", "File name", DEFAULT_FILENAME),
                    (gimpfu.PF_DIRNAME, "folder", "Output Folder", DEFAULT_OUTPUT_DIR),
                    (gimpfu.PF_BOOL, "dithering", "Dithering", True),
                    (gimpfu.PF_BOOL, "exp-pal", "Export palette", False),
                    (gimpfu.PF_BOOL, "skip_index0", "Reserve index 0 for transparency", True),
                    (gimpfu.PF_COLOR, "trans_color", "Input transparent color", (0xff, 0x0, 0xff)),
                    (gimpfu.PF_RADIO, "image-enc", "Image Encoding", DEFAULT_OUTPUT_FMT,
                       (("Binary format with palette (SC5)", "SC5"),
                        ("Binary format without palette (SR5)", "SR5"),
                        ("MSX-BASIC COPY to disk (no palette)", "DAT"),
                        ("Raw file (no palette)", "RAW"),
                        ("No output (image in new window)", "no-output"))),
                    (gimpfu.PF_BOOL, "paltxt_file", "Export plain text palette", False)
                ],
                [],
                write_g4)

gimpfu.register("msx_g4_exporter_alpha",
                "Export RGBA image to MSX2 GRAPHICS 4 bitmap (a.k.a. SCREEN 5 in BASIC)",
                "Export MSX-compatible image",
                "Pedro de Medeiros", "Pedro de Medeiros", "2021-2025",
                "<Image>/Filters/MSX/Export GRAPHICS 4 bitmap (with alpha channel)...",
                "RGBA, INDEXEDA, GRAYA", [
                    (gimpfu.PF_STRING, "filename", "File name", DEFAULT_FILENAME),
                    (gimpfu.PF_DIRNAME, "folder", "Output Folder", DEFAULT_OUTPUT_DIR),
                    (gimpfu.PF_BOOL, "dithering", "Dithering", True),
                    (gimpfu.PF_BOOL, "exp-pal", "Export palette", False),
                    (gimpfu.PF_RADIO, "image-enc", "Image Encoding", DEFAULT_OUTPUT_FMT,
                       (("Binary format with palette (SC5)", "SC5"),
                        ("Binary format without palette (SR5)", "SR5"),
                        ("MSX-BASIC COPY to disk (no palette)", "DAT"),
                        ("Raw file (no palette)", "RAW"),
                        ("No output (image in new window)", "no-output"))),
                    (gimpfu.PF_BOOL, "paltxt-file", "Export plain text palette", False)
                ],
                [],
                write_g4_alpha)

gimpfu.main()
