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
import threading
import traceback


# constants
MAX_COLORS = 16
MAX_WIDTH = 256
MAX_HEIGHT = 256
MAX_PAGES = 4
ENC_GROUP = {0: "SC5", 1: "SR5", 2: "DAT", 3: "RAW", 4: "no-output" }
BIN_PREFIX = 0xFE
DEFAULT_FILENAME = 'NONAME'
PALETTE_OFFSET = 0x7680

# exit status results
UNFINISHED = -1
SUCCESS = 0
ERROR = 1
DUPLICATE = 2


# translation functions
def N_(message): return message
def _(message): return GLib.dgettext(None, message)


class InvalidAlphaValueError(Exception):
    pass


class ImageFormatError(Exception):
    pass


def on_combo_changed(combo, affected_element):
    affected_element.set_sensitive(False if combo.get_active() == 1 else True)


def normalize(color):
    return int(color.red * 255), int(color.green * 255), int(color.blue * 255), int(color.alpha * 255)


def compress_rgba(pixel):
    r = int(round(float(pixel[0]) / 0xff * 7)) << 5
    g = int(round(float(pixel[1]) / 0xff * 7)) << 5
    b = int(round(float(pixel[2]) / 0xff * 7)) << 5
    return (r, g, b, 255)


def scatter_noise(plugin, x, y, error):
    NEIGHBORS = ( (+1, 0, 7.0/16), (-1, +1, 3.0/16), (-1, +1, 5.0/16), (+1, +1, 1.0/16) )
    for offset_x, offset_y, debt in NEIGHBORS:
        off_x, off_y = x + offset_x, y + offset_y
        if off_x < 0 or off_y < 0 or off_x >= plugin.width or off_y >= plugin.height:
            continue
        pixel = plugin.get_pixel(off_x, off_y)[0:3]
        npixel = tuple(max(0, min(255, round(color + error * debt))) for color, error in zip(pixel, error))
        #print('pos:', (off_x, off_y), ':', pixel, '->', npixel)
        plugin.set_pixel(off_x, off_y, npixel + (, 255))


def downsampling(plugin, trans_color, dithering):
    plugin.set_progress(text=_("Downsampling..."))
    for y in range(plugin.height):
        for x in range(plugin.width):
            c = plugin.get_pixel(x, y)
            if c[0:3] != trans_color[0:3]:
                d = compress_rgba(c)
                plugin.set_pixel(x, y, d)
                if dithering:
                    # ignore alpha channel in c and d
                    error = [old - new for old, new in zip(c[0:3], d[0:3])]
                    scatter_noise(plugin, x, y, error)
        plugin.set_progress(y / plugin.height)


def create_histogram(plugin, trans_color):
    histogram = {}
    plugin.set_progress(text=_('Creating histogram...'))
    for y in range(plugin.height):
        for x in range(plugin.width):
            c = plugin.get_pixel(x, y)
            if c == trans_color: continue
            histogram[(c[0], c[1], c[2])] = histogram.get((c[0], c[1], c[2]), 0) + 1
        plugin.set_progress(y / plugin.height)
    return histogram.items()


def distance(src, dst):
    r1, g1, b1 = src
    r2, g2, b2 = dst
    return sqrt((r1 - r2) ** 2 + (g1 - g2) ** 2 + (b1 - b2) ** 2)


tuple_key = lambda pair: pair[0]
tuple_value = lambda pair: pair[1]


def quantize_colors(plugin, histogram, length):
    """Group similar colours reducing palette to "length"."""
    palette = []
    hist = list(histogram)
    plugin.set_progress(text="Quantizing colors...")
    while len(hist) > length:
        # Order histogram by color usage (this is slooooooow!)
        hist = sorted(hist, key=tuple_value)
        #histogram.sort(key=tuple_value)

        # Get least frequent item and its nearest cousin by colour
        color1, freq = hist.pop(0)
        distances = [
            (idx, distance(color1, color2)) for idx, (color2, _) in enumerate(hist[1:], start=1)
        ]
        index, __ = min(distances, key=tuple_value)

        # add removed item's frequency into nearest cousin's frequency
        hist[index] = (hist[index][0], hist[index][1] + freq)
        plugin.set_progress(len(hist) / length)

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


def preprocess_image(plugin, trans_color):
    """Pre-process image and gather all used colors."""
    colormap = {}
    used_transparency = False
    plugin.set_progress(text=_("Pre-processing image..."))
    for y in range(0, plugin.height):
        for x in range(0, plugin.width):
            r, g, b, a = plugin.get_pixel(x, y)
            if not a in (0, 255):
                raise InvalidAlphaValueError(_("Invalid alpha value {} (0 or 255 expected).").format(a))
            elif a == 0:
                if not trans_color:
                    raise NoTransparentColor(_("Transparent pixel not expected but found in image."))
                plugin.set_pixel(x, y, trans_color)
                colormap[(r, g, b)] = colormap.get(trans_color, 0) + 1
                used_transparency = 1
            elif (r, g, b) == trans_color:
                used_transparency = 1
            else:
                colormap[(r, g, b)] = colormap.get((r, g, b), 0) + 1
        plugin.set_progress(y / plugin.height)
    return used_transparency, colormap


def convert(plugin, *args):
    try:
        do_convert(plugin, *args)
    except Exception as e:
        message = ''.join(traceback.format_exception(type(e), e, e.__traceback__))
        plugin.throw(message)

def do_convert(plugin, filename, folder, dithering, export_pal, skip_index0, trans_color, encoding, pal_file):
    # transparent color ignored if not required
    if not skip_index0:
        trans_color = [None, None, None]
        max_colors = MAX_COLORS
    else:
        max_colors = MAX_COLORS - 1

    if not encoding in ("DAT", "no-output") and plugin.width != MAX_WIDTH:
        raise ImageFormatError(_("Width should be exactly 256 (currently {}) for this image type.").format(plugin.width))
    elif encoding != "no-output" and plugin.width > MAX_WIDTH:
        raise ImageFormatError(_("Width should not be greater than 256 (currently {}) for this image type.").format(plugin.width))
    elif encoding != "no-output" and plugin.height > MAX_HEIGHT:
        raise ImageFormatError(_("Height should not be greater than 256 (currently {}) for this image type.").format(plugin.height))

    if plugin.height > MAX_HEIGHT * MAX_PAGES:
        raise ImageFormatError(_("Height must not be greater than {}.").format(MAX_HEIGHT * MAX_PAGES))

    # create palette data
    pal9bits = [0] * 2 * MAX_COLORS
    txtpal = [(0, 0, 0)] * MAX_COLORS

    used_transparency, colormap = preprocess_image(plugin, trans_color)
    if not used_transparency:
        # transparent color ignored if not used
        trans_color = [None, None, None]
    else:
        # disable dithering when transparency is used
        dithering = 0
    # downsampling happens in place
    downsampling(plugin, trans_color, dithering)
    histogram = create_histogram(plugin, trans_color)
    palette = quantize_colors(plugin, histogram, max_colors)
    query = create_distance_query(palette)

    for (r, g, b), index in palette:
        # Start palette at color 1 if transparency is used.
        i = index + used_transparency
        pal9bits[i * 2] = 16 * (r >> 5) + (b >> 5)
        pal9bits[i * 2 + 1] = (g >> 5)
        txtpal[i] = (r >> 5, g >> 5, b >> 5)

    if export_pal:
        encoded = struct.pack('<BHHH{}B'.format(len(pal9bits)), BIN_PREFIX, PALETTE_OFFSET,
                PALETTE_OFFSET + len(pal9bits), 0, *pal9bits[0:len(pal9bits)])
        with open(os.path.join(folder, '%s.PAL' % filename), 'wb') as file:
            file.write(encoded)

    plugin.set_progress(text=_("Exporting image to {} format...").format(_(encoding)));

    if encoding == 'no-output':
        # Export 16 color palette
        if pal_file:
            with open(os.path.join(folder, '%s.TXT' % filename), 'wt') as file:
                print("16-color palette:", file=file)
                for i, ((r, g, b), __) in enumerate(palette):
                    print('%i: %i, %i, %i' % (i, r, g, b), file=file)
        # complete buffer
        buffer = [0] * plugin.width * plugin.height
        for y in range(plugin.height):
            for x in range(plugin.width):
                c = plugin.get_pixel(x, y)
                if c[0:3] != trans_color[0:3]:
                    index, (r, g, b) = query((c[0], c[1], c[2]))
                #print("query = ", c[0:3], '->', (r, g, b), '[', index, ']')
                plugin.set_pixel(x, y, (r, g, b, 255))
            plugin.set_progress(y/plugin.height)
        # Display the duplicated image
        plugin.set_progress(status=(DUPLICATE, plugin.buffer))
    else:
        # Export MSX2 palette
        if pal_file:
            with open(os.path.join(folder, '%s.TXT' % filename), 'wt') as file:
                print("SCREEN 5 palette:", file=file)
                for i, (r, g, b) in enumerate(txtpal):
                    print('%i: %i, %i, %i' % (i, r, g, b), file=file)
        # complete buffer
        buffer = [0] * (plugin.width // 2) * plugin.height
        for y in range(plugin.height):
            for x in range(plugin.width):
                c = plugin.get_pixel(x, y)
                if c == trans_color:
                    # index of transparent color is always 0
                    index = 0
                else:
                    index, __ = query((c[0], c[1], c[2]))
                    index += skip_index0
                pos = x // 2 + y * (plugin.width // 2)
                buffer[pos] |= index if x % 2 else index << 4;
            plugin.set_progress(y/plugin.height)

        # Embed palette into image data (SC5 only)
        if encoding == 'SC5':
            for pos in range(32):
                buffer[0x7680 + pos] = pal9bits[pos]

        if encoding == 'RAW':
            encoded = struct.pack('<{}B'.format(len(buffer)), *buffer)
        elif encoding == 'DAT':
            encoded = struct.pack('<HH{}B'.format(plugin.width * plugin.height // 2), plugin.width, plugin.height, *buffer)
        else:
            encoded = struct.pack('<BHHH{}B'.format(len(buffer)), BIN_PREFIX, 0, len(buffer), 0, *buffer)
        with open(os.path.join(folder, '%s.%s' % (filename, encoding)), 'wb') as file:
            file.write(encoded)
        # Doing this instead of returning
        plugin.set_progress(status=(SUCCESS, None))


class Graph4Exporter (Gimp.PlugIn):
    __gtype_name__ = "msx-graph4-exporter"
    status = UNFINISHED
    done_args = ()

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
            # add alpha channel to image if needed
            if not drawables[0].has_alpha:
                self.image = image.duplicate()
                drawables = self.image.get_layers()
                drawables[0].add_alpha()
            self.image = image
            self.drawable = drawables[0]

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

            #dialog.set_modal(True)
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
            dithering_combo.append_text("Off")
            dithering_combo.append_text("On")
            dithering_combo.set_active(1)
            grid.attach(dithering_label, 0, 2, 1, 1)
            grid.attach(dithering_combo, 1, 2, 1, 1)

            # Export palette combo box
            export_label = Gtk.Label(label="Export palette")
            export_label.set_halign(Gtk.Align.END)
            export_combo = Gtk.ComboBoxText()
            export_combo.append_text("Off")
            export_combo.append_text("On")
            export_combo.set_active(1)
            grid.attach(export_label, 0, 3, 1, 1)
            grid.attach(export_combo, 1, 3, 1, 1)

            # Reserve index 0 as transparency
            index0_label = Gtk.Label(label="Reserve index 0 as transparency")
            index0_label.set_halign(Gtk.Align.END)
            index0_combo = Gtk.ComboBoxText()
            index0_combo.append_text("Off")
            index0_combo.append_text("On")
            index0_combo.set_active(1)
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
            pal_combo.append_text("Off")
            pal_combo.append_text("On")
            pal_combo.set_active(0)
            grid.attach(pal_label, 0, 7, 1, 1)
            grid.attach(pal_combo, 1, 7, 1, 1)

            # Connect the index0 "changed" signal to trans_button
            index0_combo.connect("changed", on_combo_changed, trans_button)

            dialog.show_all()
            response = dialog.run()
            if response == Gtk.ResponseType.OK:
                # keep current dialog around
                [item.set_sensitive(False) for item in grid.get_children()]
                file_value = file_entry.get_text()
                folder_value = folder_button.get_filename()
                dithering_value = dithering_combo.get_active()
                export_value = export_combo.get_active()
                index0_value = index0_combo.get_active()
                trans_color = normalize(trans_button.get_rgba())
                encoding_value = ENC_GROUP[[item.get_active() for item in radio_box.get_children()].index(1)]
                pal_value = pal_combo.get_active()
                dialog.destroy()

                # progress dialog will bock
                result = self.convert(PluginConnector(self),
                    file_value, folder_value, dithering_value, export_value,
                    index0_value, trans_color, encoding_value, pal_value)
                return procedure.new_return_values(*result)
            else:
                dialog.destroy()
                return procedure.new_return_values(*result)

    def convert(self, *args):
        from gi.repository import Gtk
        self.dialog = Gtk.Dialog(
            title="MSX Graphics 4 Image Converter Progress",
            use_header_bar=False,
            transient_for=None,
            role="msx-graph4-exporter-progress",
            flags=0,
        )
        self.dialog.set_modal(True)

        # Grid
        grid = Gtk.Grid(column_spacing=10, row_spacing=10, margin=10)
        content_area = self.dialog.get_content_area()
        content_area.add(grid)

        # Progress bar
        self.progress_bar = Gtk.ProgressBar.new()
        self.progress_bar.set_fraction(0.0)
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_sensitive(False)
        grid.attach(self.progress_bar, 0, 0, 1, 1)

        threading.Thread(target=convert, args=args).start()

        # block interface until done
        self.dialog.show_all()
        self.dialog.run()
        if self.status == UNFINISHED:
            return (Gimp.PDBStatusType.SUCCESS, GLib.Error())
        if self.status == ERROR:
            return (
                Gimp.PDBStatusType.CALLING_ERROR,
                GLib.Error.new_literal(Gimp.PlugIn.error_quark(), self.done_args, 0))
        if self.status == DUPLICATE:
            # arg is a new image buffer
            image = self.image.duplicate()
            drawable = image.get_layers()[0]
            self.update_drawable(drawable, self.done_args)
            image.flatten()
            Gimp.Display.new(image)
            return (Gimp.PDBStatusType.SUCCESS, GLib.Error())
        # arg is the buffer replacement
        return (Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def update_drawable(self, drawable, buf):
        """buf parameter is a list of (r, g, b, a) of the whole image."""
        buffer = drawable.get_buffer()
        # convert back list of tuples of 4 bytes to bytearray
        flat_list = [channel for pixel in buf for channel in pixel]
        buffer.set(Gegl.Rectangle.new(0, 0, drawable.get_width(), drawable.get_height()), "R'G'B'A u8",
            bytearray(flat_list))

    def done(self, *args):
        self.status, self.done_args = args
        self.dialog.destroy()


class PluginConnector:
    def __init__(self, plugin):
        self.plugin = plugin                    # will never call it directly
        self.width = plugin.drawable.get_width()
        self.height = plugin.drawable.get_height()
        # convert bytearray buffer in list of tuples of 4 bytes
        tmp = plugin.drawable.get_buffer().get(Gegl.Rectangle.new(0, 0, self.width, self.height),
            1.0, "R'G'B'A u8", Gegl.AUTO_ROWSTRIDE)
        self.buffer = [tuple(tmp[i : i + 4]) for i in range(0, len(tmp), 4)]

    def set_progress(self, fraction=0.0, text=None, status=None):
        if status != None:
            plugin = self.plugin
            GLib.idle_add(lambda: plugin.done(*status))
        else:
            progress_bar = self.plugin.progress_bar
            GLib.idle_add(lambda: progress_bar.set_fraction(fraction))
            if text: GLib.idle_add(lambda: progress_bar.set_text(text))

    def set_pixel(self, x, y, pixel):
        self.buffer[x + y * self.width] = pixel

    def get_pixel(self, x, y):
        return self.buffer[x + y * self.width]

    def throw(self, e):
        plugin = self.plugin
        GLib.idle_add(lambda: plugin.done(ERROR, e))

Gimp.main(Graph4Exporter.__gtype__, sys.argv)
