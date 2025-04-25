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

import os
import sys


# constants
MAX_COLORS = 16
MAX_WIDTH = 256
MAX_HEIGHT = 256


def N_(message): return message
def _(message): return GLib.dgettext(None, message)


def on_combo_changed(combo, affected_element):
    affected_element.set_sensitive(False if combo.get_active() == 1 else True)


class InvalidAlphaValueError(Exception):
    pass
class ImageFormatError(Exception):
    pass


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
            file_entry.set_text("NONAME")
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
            plain_label = Gtk.Label(label="Export plain text palette")
            plain_label.set_halign(Gtk.Align.END)
            plain_combo = Gtk.ComboBoxText()
            plain_combo.append_text("On")
            plain_combo.append_text("Off")
            plain_combo.set_active(1)
            grid.attach(plain_label, 0, 7, 1, 1)
            grid.attach(plain_combo, 1, 7, 1, 1)

            # Connect the index0 "changed" signal to trans_button
            index0_combo.connect("changed", on_combo_changed, trans_button)

            dialog.show_all()
            response = dialog.run()
            result = Gimp.PDBStatusType.CANCEL

            if response == Gtk.ResponseType.OK:
                file_value = file_entry.get_text()
                folder_value = folder_button.get_filename()
                dithering_value = dithering_combo.get_active()
                export_value = export_combo.get_active()
                index0_value = index0_combo.get_active()
                trans_color = trans_button.get_rgba()
                encoding_value = (radio1.get_active(), radio2.get_active(), radio3.get_active(), radio4.get_active(),
                                  radio5.get_active()).index(1)
                plain_value = plain_combo.get_active()
                try:
                    result = self.convert_gr4(image, file_value, folder_value, dithering_value, export_value,
                                              index0_value, trans_color, encoding_value, plain_value)
                except Exception as e:
                    error = GLib.Error.new_literal(Gimp.PlugIn.error_quark(), str(e), 0)
                    return procedure.new_return_values(Gimp.PDBStatusType.CALLING_ERROR, error)

            dialog.destroy()
            return procedure.new_return_values(result, GLib.Error())

    def convert_gr4(self, image, file_value, folder_value, dithering_value, export_value, index0_value,
                    trans_color, encoding_value, plain_value):

        print(image, file_value, folder_value, dithering_value, export_value, index0_value, trans_color,
              encoding_value, plain_value, sep="\n")

        # transparent color ignored if specified
        if not index0_value: trans_color = False

        dup = image.duplicate()
        if not dup.get_layers()[0].has_alpha:
            dup.get_layers()[0].add_alpha()
            print(f"Added alpha to layer: {dup.get_layers()[0].get_name()}")

        drawable = dup.get_layers()[0]
        width = drawable.get_width()
        height = drawable.get_height()
        if not encoding_value in (3, 5) and width != 256:
            raise ImageFormatError(_("Width is not 256 ({}).").format(width))

        type_ = dup.get_base_type()
        print("type =", type_)
        if type_ == Gimp.ImageBaseType.INDEXED:
            len_colormap, colormap = gather_colormap(drawable)
            #dup.convert_rgb()
            print("indexed")
            #num_bytes, colormap = dup.get_colormap()
        elif type_ == Gimp.ImageBaseType.GRAY:
            #dup.convert_rgb()
            print("gray")
        else:
            len_colormap, colormap = gather_colormap(drawable)
            print("rgb*")

        # Display the duplicated image
        Gimp.Display.new(dup)

        return Gimp.PDBStatusType.SUCCESS

def gather_colormap(drawable):
    """Scan image gathering all colors used."""
    colormap = {}

    buffer = drawable.get_buffer()
    rect = Gegl.Rectangle()
    rect.x = 0
    rect.y = 0
    rect.width = drawable.get_width()
    rect.height = 1

    for y in range(0, drawable.get_height()):
        rect.y = y
        linebuf = buffer.get(rect, 1.0, "RGBA u8", Gegl.AUTO_ROWSTRIDE)
        pixels = [tuple(linebuf[i : i + 4]) for i in range(0, len(linebuf), 4)]
        for (r, g, b, a) in pixels:
            if not a in (0, 255):
                raise InvalidAlphaValueError(_("Invalid alpha value {}.").format(a))
            colormap[(r, g, b)] = colormap.get((r, g, b), 0) + 1
    return len(colormap), colormap

Gimp.main(Graph4Exporter.__gtype__, sys.argv)

