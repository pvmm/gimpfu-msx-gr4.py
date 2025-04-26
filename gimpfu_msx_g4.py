#!/usr/bin/python3
# -*- coding: utf-8 -*-
#   This program is free software: you can redistribute it and/or modify
#   it under the terms of the GNU General Public License as published by
#   the Free Software Foundation; either version 3 of the License, or
#   (at your option) any later version.
#
#   This program is distributed in the hope that it will be useful,
#   but WITHOUT ANY WARRANTY; without even the implied warranty of
#   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#   GNU General Public License for more details.
#
#   You should have received a copy of the GNU General Public License
#   along with this program.  If not, see <https://www.gnu.org/licenses/>.

import gi
gi.require_version('Gimp', '3.0')
from gi.repository import Gimp
gi.require_version('GimpUi', '3.0')
from gi.repository import GimpUi
gi.require_version('Gegl', '0.4')
from gi.repository import Gegl
from gi.repository import GObject
from gi.repository import GLib
from gi.repository import Gio
from contextlib import suppress
from math import sqrt

import os
import sys
import struct


# constants
MAX_COLORS = 16
MAX_WIDTH = 256
MAX_HEIGHT = 256
MAX_PAGES = 4
ENC_GROUP = {0: "SC5", 1: "SR5", 2: "DAT", 3: "RAW", 4: "no-output" }
BIN_PREFIX = 0xFE
DEFAULT_FILENAME = 'NONAME'
PALETTE_OFFSET = 0x7680


# translation functions
def N_(message): return message
def _(message): return GLib.dgettext(None, message)


class InvalidAlphaValueError(Exception):
    pass


class ImageFormatError(Exception):
    pass


tuple_key = lambda pair: pair[0]
tuple_value = lambda pair: pair[1]


def on_combo_changed(combo, affected_element):
    affected_element.set_sensitive(False if combo.get_active() == 1 else True)


def normalize(color):
    return int(color.red * 255), int(color.green * 255), int(color.blue * 255), int(color.alpha * 255)


def get_pixel(drawable, x, y):
    return tuple(drawable.get_buffer().get(Gegl.Rectangle.new(x, y, 1, 1), 1.0, "RGBA u8",
        Gegl.AUTO_ROWSTRIDE))


def set_pixel(drawable, x, y, pixel):
    drawable.get_buffer().set(Gegl.Rectangle.new(x, y, 1, 1), "RGBA u8", pixel)


def compress_rgba(pixel):
    r = int(round(float(pixel[0]) / 0xff * 7)) << 5
    g = int(round(float(pixel[1]) / 0xff * 7)) << 5
    b = int(round(float(pixel[2]) / 0xff * 7)) << 5
    return (r, g, b, 255)


def scatter_noise(drawable, x, y, error):
    width = drawable.get_width()
    height = drawable.get_height()

    NEIGHBORS = ( (+1, 0, 7.0/16), (-1, +1, 3.0/16), (-1, +1, 5.0/16), (+1, +1, 1.0/16) )
    for offset_x, offset_y, debt in NEIGHBORS:
        with supress(IndexError):
            off_x, off_y = x + offset_x, y + offset_y
            if off_x < 0 or off_y < 0 or off_x >= width or off_y >= height:
                continue
            c = get_pixel(drawable, off_x, off_y)
            d = tuple(max(0, min(255, round(color + error * debt))) for color, error in zip(c, error))
            #print 'pos:', (off_x + 255 * off_y), " c/d:", c, d
            set_pixel(drawable, off_x, off_y, d)


def downsampling(image, trans_color, dithering):
    drawable = image.get_layers()[0]
    buffer = drawable.get_buffer()
    x = 0
    for y in range(drawable.get_height()):
        for x in range(drawable.get_width()):
            c = get_pixel(drawable, x, y)
            if c != trans_color:
                d = compress_rgba(c)
                set_pixel(drawable, x, y, d)
                if dithering:
                    # ignore alpha channel in c and d
                    error = [old - new for old, new in zip(c[0:3], d[0:3])]
                    scatter_noise(drawable, x, y, error)
    return drawable


def create_histogram(drawable, trans_color):
    histogram = {}

    #percent = 0.0
    #step = 1.0 / drawable.get_height()

    #gimpfu.pdb.gimp_progress_init('Creating histogram...', None)
    #gimpfu.pdb.gimp_progress_update(0)

    for y in range(drawable.get_height()):
        for x in range(drawable.get_width()):
            c = get_pixel(drawable, x, y)
            if c == trans_color: continue
            histogram[(c[0], c[1], c[2])] = histogram.get((c[0], c[1], c[2]), 0) + 1

        #percent += step
        #gimpfu.pdb.gimp_progress_update(percent)

    return histogram.items()


def distance(src, dst):
    r1, g1, b1 = src
    r2, g2, b2 = dst
    return sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)


def quantize_colors(histogram, length):
    """Group similar colours reducing palette to "length"."""
    palette = []
    hist = list(histogram)
    print('histogram', hist)

    #gimpfu.pdb.gimp_progress_init('Quantizing colors...', None)
    #gimpfu.pdb.gimp_progress_update(0.0)

    #percent = 0.0
    #step = float(len(hist) - length) / 100

    while len(hist) > length:
        # Order histogram by color usage (this is slooooooow!)
        hist = sorted(hist, key=tuple_value)
        #histogram.sort(key=tuple_value)

        # Get least frequent item and its nearest cousin by colour
        color1, freq = hist.pop(0)
        distances = [
            (idx, distance(color1, color2)) for idx, (color2, _) in enumerate(hist[1:], start=1)
        ]
        index, _ = min(distances, key=tuple_value)

        # add removed item's frequency into nearest cousin's frequency
        hist[index] = (hist[index][0], hist[index][1] + freq)

        #percent += step
        #gimpfu.pdb.gimp_progress_update(percent)

    for index, (color, _) in enumerate(sorted(hist, key=tuple_key)):
        palette.append((color, index))

    return palette


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


class Graph4Exporter (Gimp.PlugIn):
    __gtype_name__ = "msx-graph4-exporter"

    ## GimpPlugIn virtual methods ##
    def do_query_procedures(self):
        return [ "msx-graph4-exporter" ]

    def do_create_procedure(self, name):
        procedure = Gimp.ImageProcedure.new(self, name,
                                            Gimp.PDBProcType.PLUGIN,
                                            self.run, None)

        procedure.set_image_types("RGB*,INDEXED*,GRAY*")
        procedure.set_sensitivity_mask(Gimp.ProcedureSensitivityMask.DRAWABLE)

        procedure.set_menu_label(_("MSX2 Graphics 4 Image Converter"))
        procedure.set_icon_name(GimpUi.ICON_GEGL)
        procedure.add_menu_path('<Image>/Filters/MSX/')

        procedure.set_documentation(_("MSX2 Graphics 4 Image Converter"),
                                    _("Converts image into a MSX2 graphics 4 (SCREEN 5) binary according to the VRAM layout"),
                                    name)
        procedure.set_attribution("Pedro de Medeiros", "© Pedro de Medeiros, 2025", "2025")

        return procedure

    def run(self, procedure, run_mode, image, drawables, config, run_data):
        if len(drawables) != 1:
            msg = _("Procedure '{}' only works with one drawable.").format(procedure.get_name())
            error = GLib.Error.new_literal(Gimp.PlugIn.error_quark(), msg, 0)
            return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, error)
        else:
            drawable = drawables[0]

        if run_mode == Gimp.RunMode.INTERACTIVE:
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            gi.require_version('Gdk', '3.0')
            from gi.repository import Gdk

            GimpUi.init("msx-graph4-exporter")

            dialog = Gtk.Dialog(
                title="MSX Graphics 4 Image Converter Options",
                use_header_bar=True,
                transient_for=None,
                role="msx-graph4-exporter",
                flags=0,
            )

            dialog.set_modal(True)
            dialog.add_buttons(
                Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                Gtk.STOCK_OK, Gtk.ResponseType.OK
            )
            dialog.set_default_response(Gtk.ResponseType.OK)

            content_area = dialog.get_content_area()
            grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
            content_area.add(grid)

            # File name
            file_label = Gtk.Label(label="File name")
            file_label.set_halign(Gtk.Align.END)
            file_entry = Gtk.Entry()
            file_entry.set_text(DEFAULT_FILENAME)
            grid.attach(file_label, 0, 0, 1, 1)
            grid.attach(file_entry, 1, 0, 1, 1)

            # Directory selector
            folder_label = Gtk.Label(label="Output folder")
            folder_label.set_halign(Gtk.Align.END)
            folder_button = Gtk.FileChooserButton(title="Choose a folder", action=Gtk.FileChooserAction.SELECT_FOLDER)
            folder_button.set_current_folder(os.getcwd())
            grid.attach(folder_label, 0, 1, 1, 1)
            grid.attach(folder_button, 1, 1, 1, 1)

            # Dithering combo box
            dithering_label = Gtk.Label(label="Dithering")
            dithering_label.set_halign(Gtk.Align.END)
            dithering_combo = Gtk.ComboBoxText()
            dithering_combo.append_text("On")
            dithering_combo.append_text("Off")
            dithering_combo.set_active(0)
            grid.attach(dithering_label, 0, 2, 1, 1)
            grid.attach(dithering_combo, 1, 2, 1, 1)

            # Export palette combo box
            export_label = Gtk.Label(label="Export palette")
            export_label.set_halign(Gtk.Align.END)
            export_combo = Gtk.ComboBoxText()
            export_combo.append_text("On")
            export_combo.append_text("Off")
            export_combo.set_active(0)
            grid.attach(export_label, 0, 3, 1, 1)
            grid.attach(export_combo, 1, 3, 1, 1)

            # Reserve index 0 as transparency
            index0_label = Gtk.Label(label="Reserve index 0 as transparency")
            index0_label.set_halign(Gtk.Align.END)
            index0_combo = Gtk.ComboBoxText()
            index0_combo.append_text("On")
            index0_combo.append_text("Off")
            index0_combo.set_active(0)
            grid.attach(index0_label, 0, 4, 1, 1)
            grid.attach(index0_combo, 1, 4, 1, 1)

            # Color picker
            trans_label = Gtk.Label(label="Input transparent color")
            trans_label.set_halign(Gtk.Align.END)
            trans_button = Gtk.ColorButton()
            default_color = Gdk.RGBA()
            default_color.parse("#ff00ff")
            trans_button.set_rgba(default_color)
            grid.attach(trans_label, 0, 5, 1, 1)
            grid.attach(trans_button, 1, 5, 1, 1)

            # Radio button group
            radio_label = Gtk.Label(label="Image encoding")
            radio_label.set_halign(Gtk.Align.END)
            radio1 = Gtk.RadioButton.new_with_label_from_widget(None, "Binary format with palette (SC5)")
            radio2 = Gtk.RadioButton.new_with_label_from_widget(radio1, "Binary format without palette (SR5)")
            radio3 = Gtk.RadioButton.new_with_label_from_widget(radio1, "MSX-BASIC COPY to disk (no palette)")
            radio4 = Gtk.RadioButton.new_with_label_from_widget(radio1, "Raw file (no palette)")
            radio5 = Gtk.RadioButton.new_with_label_from_widget(radio1, "No output (image in new window)")
            radio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
            radio_box.pack_start(radio1, False, False, 0)
            radio_box.pack_start(radio2, False, False, 0)
            radio_box.pack_start(radio3, False, False, 0)
            radio_box.pack_start(radio4, False, False, 0)
            radio_box.pack_start(radio5, False, False, 0)
            grid.attach(radio_label, 0, 6, 1, 1)
            grid.attach(radio_box, 1, 6, 1, 1)

            # Reserve index 0 as transparency
            pal_label = Gtk.Label(label="Export plain text palette")
            pal_label.set_halign(Gtk.Align.END)
            pal_combo = Gtk.ComboBoxText()
            pal_combo.append_text("On")
            pal_combo.append_text("Off")
            pal_combo.set_active(1)
            grid.attach(pal_label, 0, 7, 1, 1)
            grid.attach(pal_combo, 1, 7, 1, 1)

            # Connect the index0 "changed" signal to trans_button
            index0_combo.connect("changed", on_combo_changed, trans_button)

            dialog.show_all()
            response = dialog.run()
            result = Gimp.PDBStatusType.CANCEL

            if response == Gtk.ResponseType.OK:
                grid.set_sensitive(False)
                file_value = file_entry.get_text()
                folder_value = folder_button.get_filename()
                dithering_value = dithering_combo.get_active()
                export_value = export_combo.get_active()
                index0_value = index0_combo.get_active()
                trans_color = normalize(trans_button.get_rgba())
                encoding_value = ENC_GROUP[[item.get_active() for item in radio_box.get_children()].index(1)]
                pal_value = pal_combo.get_active()
                try:
                    result = convert(image, file_value, folder_value, dithering_value, export_value,
                                     index0_value, trans_color, encoding_value, pal_value)
                except Exception as e:
                    error = GLib.Error.new_literal(Gimp.PlugIn.error_quark(), str(e), 0)
                    return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, error)

            dialog.destroy()
            return procedure.new_return_values(result, GLib.Error())

def convert(image, filename, folder, dithering, export_pal, skip_index0,
            trans_color, encoding, pal_file):
    # transparent color ignored if not required
    if not skip_index0:
        trans_color = None
        max_colors = MAX_COLORS
    else:
        max_colors = MAX_COLORS - 1

    # add alpha channel to image
    dup = image.duplicate()
    if not dup.get_layers()[0].has_alpha:
        dup.get_layers()[0].add_alpha()
        print(f"Added alpha to layer: {dup.get_layers()[0].get_name()}")

    drawable = dup.get_layers()[0]
    width = drawable.get_width()
    height = drawable.get_height()
    if not encoding in ("DAT", "no-output") and width != MAX_WIDTH:
        raise ImageFormatError(_("Width should be exactly 256 (currently {}) for this image type.").format(width))
    elif encoding != "no-output" and width > MAX_WIDTH:
        raise ImageFormatError(_("Width should not be greater than 256 (currently {}) for this image type.").format(width))
    elif encoding != "no-output" and height > MAX_HEIGHT:
        raise ImageFormatError(_("Height should not be greater than 256 (currently {}) for this image type.").format(height))

    if height > MAX_HEIGHT * MAX_PAGES:
        raise ImageFormatError(_("Height must not be greater than {}.").format(MAX_HEIGHT * MAX_PAGES))

    # create palette data
    pal9bits = [0] * 2 * MAX_COLORS
    txtpal = [(0, 0, 0)] * MAX_COLORS

    used_transparency, colormap = preprocess_image(drawable, trans_color)
    if not used_transparency:
        # transparent color ignored if not used
        trans_color = None
    else:
        # disable dithering when transparency is used
        dithering = 0
    print("downsampling...")
    drawable = downsampling(dup, trans_color, dithering)
    print("downsampling applied.")
    histogram = create_histogram(drawable, trans_color)
    print("creating histogram...")
    palette = quantize_colors(histogram, max_colors)
    print("histogram created.", palette)
    query = create_distance_query(palette)

    for (r, g, b), index in palette:
        # Start palette at color 1 if transparency is used.
        i = index + used_transparency
        pal9bits[i * 2] = 16 * (r >> 5) + (b >> 5)
        pal9bits[i * 2 + 1] = (g >> 5)
        txtpal[i] = (r >> 5, g >> 5, b >> 5)

    if pal_file:
        with open(os.path.join(folder, '%s.TXT' % filename), 'wt') as file:
            print("SCREEN 5 palette:", file=file)
            for i, (r, g, b) in enumerate(txtpal):
                print('%i: %i, %i, %i' % (i, r, g, b), file=file)

    if export_pal:
        encoded = struct.pack('<BHHH{}B'.format(len(pal9bits)), BIN_PREFIX, PALETTE_OFFSET,
                PALETTE_OFFSET + len(pal9bits), 0, *pal9bits[0:len(pal9bits)])
        with open(os.path.join(folder, '%s.PAL' % filename), 'wb') as file:
            file.write(encoded)

    #gimpfu.pdb.gimp_progress_init('Exporting image to %s format...' % encoding, None)
    #gimpfu.pdb.gimp_progress_update(0)
        
    # complete buffer size
    buffer = [0] * (width // 2) * height
    #step = 1.0 / height
    #percent = 0.0

    if encoding != 'no-output':
        for y in range(height):
            for x in range(width):
                c = get_pixel(drawable, x, y)
                if c == trans_color:
                    # index of transparent color is always 0
                    index = 0
                else:
                    index, _ = query((c[0], c[1], c[2]))
                    index += skip_index0
                pos = x // 2 + y * (width // 2)
                buffer[pos] |= index if x % 2 else index << 4;

            #percent += step
            #gimpfu.pdb.gimp_progress_update(percent)

        # Embed palette into image data (SC5 only)
        if encoding == 'SC5':
            for pos in range(32):
                buffer[0x7680 + pos] = pal9bits[pos]

        if encoding == 'RAW':
            encoded = struct.pack('<{}B'.format(len(buffer)), *buffer)
        elif encoding == 'DAT':
            encoded = struct.pack('<HH{}B'.format(width * height // 2), width, height, *buffer)
        else:
            encoded = struct.pack('<BHHH{}B'.format(len(buffer)), BIN_PREFIX, 0, len(buffer), 0, *buffer)
        with open(os.path.join(folder, '%s.%s' % (filename, encoding)), 'wb') as file:
            file.write(encoded)

        # Delete scratch image
        dup.delete()
    else:
        # Display the duplicated image
        Gimp.Display.new(dup)

    return Gimp.PDBStatusType.SUCCESS


def preprocess_image(drawable, trans_color):
    """Pre-process image and gather all used colors."""
    colormap = {}
    used_transparency = False
    buffer = drawable.get_buffer()
    rect = Gegl.Rectangle.new(0, 0, drawable.get_width(), 1)
    for y in range(0, drawable.get_height()):
        rect.y = y
        linebuf = buffer.get(rect, 1.0, "RGBA u8", Gegl.AUTO_ROWSTRIDE)
        pixels = [tuple(linebuf[i : i + 4]) for i in range(0, len(linebuf), 4)]
        for (r, g, b, a) in pixels:
            if not a in (0, 255):
                raise InvalidAlphaValueError(_("Invalid alpha value {} (0 or 255 expected).").format(a))
            elif a == 0:
                if not trans_color:
                    raise NoTransparentColor(_("Transparent pixel not expected but found in image."))
                set_pixel(drawable, x, y, trans_color)
                colormap[(r, g, b)] = colormap.get(trans_color, 0) + 1
                used_transparency = 1
            elif (r, g, b) == trans_color:
                used_transparency = 1
            else:
                colormap[(r, g, b)] = colormap.get((r, g, b), 0) + 1
    return used_transparency, colormap


Gimp.main(Graph4Exporter.__gtype__, sys.argv)

