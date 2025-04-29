#!/usr/bin/env python
'''
Created on 2021/11/30

@author: Pedro de Medeiros <pedro.medeiros@gmail.com>

Installation: 
    - For GIMP2, put this file into your GIMP plugin directory, i.e. ~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/gimp2_msx_gr4.py
    - For GIMP3, put this file inside a subdirectory with the same name in your plugin directory, i.e. ~/.var/app/org.gimp.GIMP/config/GIMP/2.10/plug-ins/gimp3_msx_gr4/gimp3_msx_gr4.py
    - Restart Gimp
    - Run script via Filters/MSX/Export GRAPHICS 4 bitmap...
'''

from __future__ import print_function
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
        #print('pixel =', pixel, ' distances =', dsts, ' min =', mdst)
        palmap[pixel] = mdst[0]
        return mdst[0], mdst[2]

    return query_index


class ImagePlugin:
    """Image Plugin gathers useful functions for RGB* and indexed images"""
    def __init__(self, image, has_transparency, trans_color = False):
        self.type = gimpfu.pdb.gimp_image_base_type(image);
        self.has_transparency = has_transparency
        if self.type == RGB:
            drawable = gimpfu.pdb.gimp_image_active_drawable(image)
            nchannels, _ = gimpfu.pdb.gimp_drawable_get_pixel(drawable, 0, 0)
            def downsampling(self, pixel):
                r = int(round(float(pixel[0]) / 0xff * 7)) << 5
                g = int(round(float(pixel[1]) / 0xff * 7)) << 5
                b = int(round(float(pixel[2]) / 0xff * 7)) << 5
                return (r, g, b, 255)
            # downsampling defined only for RGB* mode
            self.downsampling = downsampling
            if trans_color:
                self.trans_color = trans_color[0:4] if self.has_transparency else False
                def is_transparent(self, pixel):
                    return pixel[0:3] == self.trans_color[0:3] or pixel[3] == 0
            else:
                # default to "invisible black"
                self.trans_color = (0, 0, 0, 0) if self.has_transparency else False
                def is_transparent(self, pixel):
                    # is alpha channel completely transparent?
                    return pixel[3] == 0
            self.is_transparent = is_transparent

        elif type_ == INDEXED:
            trans_index = False
            trans_count = 0
            num_bytes, colormap = gimpfu.pdb.gimp_image_get_colormap(image)
            for i, c in enumerate(range(0, num_bytes, 3)):
                if (colormap[c], colormap[c + 1], colormap[c + 2]) == trans_color:
                    trans_index = i
                    trans_count += 1
            if trans_count > 1:
                # Is it possible to register same color twice in colormap?
                raise Exception("More than one transparent color detected.")
            # Use index of transparent color
            self.trans_color = trans_index
            def is_transparent(self, pixel):
                return self.has_transparency and pixel == trans_index
            self.is_transparent = is_transparent


def check_params(image, filename, folder, image_enc, exp_pal, exp_ptp):
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)

    # In screen 5 only even sizes are permitted.
    width, height = gimpfu.pdb.gimp_drawable_width(drawable) & ~1, gimpfu.pdb.gimp_drawable_height(drawable) & ~1

    errors = []
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
        errors.append('compression is not implemented yet.')

    if errors:
        return errors

    return []


def write_gr4_alpha(image, layer, filename, folder, dithering, exp_pal, image_enc, exp_ptp):
    '''
    Export image with alpha to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param dithering: whether dithering is active
    @param exp_pal: export palette data too
    @param image_enc: output encoding
    @param exp_ptp: export plain-text-palette data too
    '''
    write_gr4(image, layer, filename, folder, dithering, exp_pal, True, False, image_enc, exp_ptp)


def write_gr4(image, layer, filename, folder, dithering, exp_pal, has_transparency, trans_color, image_enc, exp_ptp):
    '''
    Export image to GRAPHICS 4, a.k.a. SCREEN 5 (MSX2).
    
    @param img: gimp image
    @param layer: gimp layer (or drawable)
    @param filename: file name
    @param folder: output directory
    @param dithering: whether dithering is active
    @param exp_pal: export palette data too
    @param has_transparency: transparency support consumes color index 0
    @param trans_color: RGB components of input color to be considered transparency
    @param image_enc: output encoding
    @param exp_ptp: export plain-text-palette data too
    '''
    errors = check_params(image, filename, folder, image_enc, exp_pal, exp_ptp)
    if errors:
        gimp.message("\n".join(errors))
        return

    # If there is no transparency, there is no transparent color
    if not has_transparency:
        trans_color = False

    filename = filename.upper()

    # Create temporary image with alpha channel
    new_image = gimpfu.pdb.gimp_image_duplicate(image)
    if not new_image.layers[0].has_alpha:
        new_image.layers[0].add_alpha()
    drawable = gimpfu.pdb.gimp_image_active_drawable(new_image)
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)

    # Check if image is indexed and convert to RGB if necessary.
    palette = []
    max_colors = MAX_COLORS - has_transparency
    type_ = gimpfu.pdb.gimp_image_base_type(new_image);
    if type_ == INDEXED:
        num_bytes, colormap = gimpfu.pdb.gimp_image_get_colormap(new_image)
        # Convert to RGB to reduce color count. Old palette is discarded.
        if num_bytes // 3 > max_colors or dithering:
            type_ = RGB
            palette = []
            gimpfu.pdb.gimp_image_convert_rgb(new_image);
    elif type_ == GRAY:
        type_ = RGB
        gimpfu.pdb.gimp_image_convert_rgb(new_image);

    try:
        plugin = ImagePlugin(new_image, has_transparency, trans_color)
    except Exception as e:
        gimp.message(e.args[0])
        gimp.delete(new_image)
        return

    # create palette data
    pal9bits = [0] * 2 * MAX_COLORS
    txtpal = [(0, 0, 0)] * MAX_COLORS

    if not palette:
        # disable dithering when transparency is used
        use_transparency = check_transparency(plugin, new_image)
        try:
            drawable = downsampling(plugin, new_image, use_transparency, dithering)
        except TypeError:
            gimp.message('Wrong plugin: alpha channel is being used.')
            gimp.delete(new_image)
            return
        #gimpfu.pdb.gimp_display_new(new_image) # disply downsampled image
        histogram = create_histogram(plugin, drawable)
        palette = quantize_colors(histogram, max_colors)
        query = create_distance_query(palette)

    for (r, g, b), index in palette:
        # Start palette at color 1 if transparency is set.
        i = index + has_transparency
        pal9bits[i * 2] = 16 * (r >> 5) + (b >> 5)
        pal9bits[i * 2 + 1] = (g >> 5)
        txtpal[i] = (r >> 5, g >> 5, b >> 5)

    if exp_ptp:
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
                    if plugin.is_transparent(plugin, c):
                        # index of transparent color is always 0
                        index = 0
                    else:
                        index, _ = query((c[0], c[1], c[2]))
                        index += has_transparency
                else:
                    # num_channels, index, alpha
                    _, (index, _) = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
                    index += has_transparency
                pos = x // 2 + y * (width // 2)
                buffer[pos] |= index if x % 2 else index << 4;

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
        gimp.delete(new_image)
    else:
        gimpfu.pdb.gimp_display_new(new_image)


def check_transparency(plugin, image):
    if not plugin.has_transparency:
        return False

    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)

    percent = 0.0
    gimpfu.pdb.gimp_progress_init('Checking image alpha channel...', None)
    gimpfu.pdb.gimp_progress_update(percent)
    step = 1.0 / height

    for y in range(height):
        for x in range(width):
            _, c = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            if plugin.is_transparent(plugin, c):
                return True
        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    return False


def create_histogram(plugin, drawable):
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)
    histogram = {}

    percent = 0.0
    step = 1.0 / height

    gimpfu.pdb.gimp_progress_init('Creating histogram...', None)
    gimpfu.pdb.gimp_progress_update(0)

    for y in range(height):
        for x in range(width):
            _, c = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            if plugin.is_transparent(plugin, c):
                continue
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
            npixel = tuple(max(0, min(255, round(color + error * debt))) for color, error in zip(pixel[0:3], error))
            #print('pos:', (off_x, off_y), ':', pixel[0:3], "->", npixel)
            gimpfu.pdb.gimp_drawable_set_pixel(drawable, off_x, off_y, nchannels, npixel)

        except Exception:
            pass # hey, gimp developers, Python 2.7 sucks!


def downsampling(plugin, image, use_transparent = False, dithering = True):
    """Reduction to 9-bit palette with optional dithering."""
    drawable = gimpfu.pdb.gimp_image_active_drawable(image)
    width, height = gimpfu.pdb.gimp_drawable_width(drawable), gimpfu.pdb.gimp_drawable_height(drawable)
 
    # Disable dithering if transparent color is used
    dithering = not use_transparent and dithering

    # Update progress bar
    gimpfu.pdb.gimp_progress_init('Downsampling%s...' % (' with dithering (slow!)' if dithering else ''), None)
    gimpfu.pdb.gimp_progress_update(0.0)

    percent = 0.0
    step = 1.0 / height

    for y in range(height):
        for x in range(width):
            nchannels, c = gimpfu.pdb.gimp_drawable_get_pixel(drawable, x, y)
            if plugin.is_transparent(plugin, c):
                d = plugin.trans_color
            else:
                d = plugin.downsampling(plugin, c)
            gimpfu.pdb.gimp_drawable_set_pixel(drawable, x, y, nchannels, d)

            if dithering:
                # ignore alpha channel in c and d
                error = [old - new for old, new in zip(c[0:3], d[0:3])]
                scatter_noise(drawable, x, y, error)

        percent += step
        gimpfu.pdb.gimp_progress_update(percent)

    return drawable


gimpfu.register("msx_gr4_exporter",
                "Export GRAPHICS 4 bitmap (a.k.a. SCREEN 5 in BASIC)",
                "Export MSX-compatible image",
                "Pedro de Medeiros", "Pedro de Medeiros", "2021-2025",
                "<Image>/Filters/MSX/Export GRAPHICS 4 bitmap...",
                "RGB*, INDEXED*, GRAY*", [
                    (gimpfu.PF_STRING, "filename", "File name", DEFAULT_FILENAME),
                    (gimpfu.PF_DIRNAME, "folder", "Output Folder", DEFAULT_OUTPUT_DIR),
                    (gimpfu.PF_BOOL, "dithering", "Dithering", True),
                    (gimpfu.PF_BOOL, "exp-pal", "Export palette", False),
                    (gimpfu.PF_BOOL, "has_transparency", "Reserve index 0 as transparency", True),
                    (gimpfu.PF_COLOR, "trans_color", "Input transparent color", (0xff, 0x0, 0xff)),
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

gimpfu.register("msx_gr4_exporter_alpha",
                "Export MSX2 GRAPHICS 4 bitmap with alpha (a.k.a. SCREEN 5 in BASIC)",
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
                    (gimpfu.PF_BOOL, "exp-ptp", "Export plain text palette", False)
                ],
                [],
                write_gr4_alpha)

gimpfu.main()
