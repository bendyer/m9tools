# Copyright (C) 2012 Ben Dyer
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to
# deal in the Software without restriction, including without limitation the
# rights to use, copy, modify, merge, publish, distribute, sublicense, and/or
# sell copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
# FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS
# IN THE SOFTWARE.
#
# Some code derived from the Python Imaging Library.
#
# Portions are:
# Copyright (c) 1997-2009 by Secret Labs AB.  All rights reserved.
# Copyright (c) 1995-2009 by Fredrik Lundh.
#
# See the LICENSE file for information on usage and redistribution.
#

from __future__ import print_function

try:
    import warnings
except ImportError:
    warnings = None


import os
import io
import sys
import array
import string
import struct
import numbers
import traceback
import collections

import logging as log
import mdng.tags as tifftags

MAXBLOCK = 65536
DEBUG = 1

II = b"II" # little-endian (intel-style)
MM = b"MM" # big-endian (motorola-style)

ol16 = lambda i: struct.pack("<H", int(i))
ol32 = lambda i: struct.pack("<L", int(i))
ob16 = lambda i: struct.pack(">H", int(i))
ob32 = lambda i: struct.pack(">L", int(i))

il16 = lambda c, o=0: struct.unpack("<H", c[o:o+2])[0]
il32 = lambda c, o=0: struct.unpack("<L", c[o:o+4])[0]
ib16 = lambda c, o=0: struct.unpack(">H", c[o:o+2])[0]
ib32 = lambda c, o=0: struct.unpack(">L", c[o:o+2])[0]

# a few tag names, just to make the code below a bit more readable
STRIPOFFSETS = 273
STRIPBYTECOUNTS = 279
SUBIFDS = 330
EXIFIFD = 34665
XMP = 700

PREFIXES = [b"MM\000\052", b"II\052\000", b"II\xBC\000"]


##
# Wrapper for TIFF IFDs.

class ImageFileDirectory(object):

    # represents a TIFF tag directory.  to speed things up,
    # we don't decode tags unless they're asked for.

    def __init__(self, prefix):
        self.prefix = prefix[:2]
        if self.prefix == MM:
            self.o16, self.o32 = ob16, ob32
            self.i16, self.i32 = ib16, ib32
            self.endian = ">"
        elif self.prefix == II:
            self.o16, self.o32 = ol16, ol32
            self.i16, self.i32 = il16, il32
            self.endian = "<"
        else:
            raise SyntaxError("not a TIFF IFD")
        self.reset()

    def reset(self):
        self.tagdata = {}
        self.tagtype = {} # added 2008-06-05 by Florian Hoech
        self.next = None

    # Base primitive
    def unknown(self, data):
        raise RuntimeError("Could not process data: " + repr(data))

    # Load primitives
    def load_unsigned_byte(self, data):
        return struct.unpack("%dB" % len(data), data)

    def load_string(self, data):
        return data

    def load_unsigned_short(self, data):
        return struct.unpack("%s%dH" % (self.endian, len(data) / 2), data)

    def load_unsigned_long(self, data):
        return struct.unpack("%s%dL" % (self.endian, len(data) / 4), data)

    def load_unsigned_rational(self, data):
        l = struct.unpack("%s%dL" % (self.endian, len(data) / 4), data)
        # Pair numerators and denominators
        return tuple((n, d) for n, d in zip(l[:-1], l[1:]))

    def load_signed_byte(self, data):
        return struct.unpack("%db" % len(data), data)

    def load_undefined(self, data):
        return data

    def load_signed_short(self, data):
        return struct.unpack("%s%dh" % (self.endian, len(data) / 2), data)

    def load_signed_long(self, data):
        return struct.unpack("%s%dl" % (self.endian, len(data) / 4), data)

    def load_signed_rational(self, data):
        l = struct.unpack("%s%dl" % (self.endian, len(data) / 4), data)
        # Pair numerators and denominators
        return tuple((n, d) for n, d in zip(l[:-1], l[1:]))

    def load_float(self, data):
        return struct.unpack("%s%df" % (self.endian, len(data) / 4), data)

    def load_double(self, data):
        return struct.unpack("%s%dd" % (self.endian, len(data) / 8), data)

    def load_ifds(self, data):
        return load_unsigned_long(self, data)

    load_dispatch = {
        1: (1, load_unsigned_byte),
        2: (1, load_string),
        3: (2, load_unsigned_short),
        4: (4, load_unsigned_long),
        5: (8, load_unsigned_rational),
        6: (1, load_signed_byte),
        7: (1, load_undefined),
        8: (2, load_signed_short),
        9: (4, load_signed_long),
        10: (8, load_signed_rational),
        11: (4, load_float),
        12: (8, load_double),
        13: (4, load_ifds)
    }

    # Store primitives
    def store_unsigned_byte(self, values):
        return struct.pack("%dB" % len(values), *values)

    def store_string(self, value):
        return value

    def store_unsigned_short(self, values):
        return struct.pack("%s%dH" % (self.endian, len(values)), *values)

    def store_unsigned_long(self, values):
        return struct.pack("%s%dL" % (self.endian, len(values)), *values)

    def store_signed_byte(self, values):
        return struct.pack("%db" % len(values), *values)

    def store_undefined(self, value):
        return value

    def store_signed_short(self, values):
        return struct.pack("%s%dh" % (self.endian, len(values)), *values)

    def store_signed_long(self, values):
        return struct.pack("%s%dl" % (self.endian, len(values)), *values)

    def store_float(self, values):
        return struct.pack("%s%df" % (self.endian, len(values)), *values)

    def store_double(self, values):
        return struct.pack("%s%dd" % (self.endian, len(values)), *values)

    def store_ifds(self, values):
        return store_unsigned_long(self, values)

    store_dispatch = {
        1: store_unsigned_byte,
        2: store_string,
        3: store_unsigned_short,
        4: store_unsigned_long,
        5: unknown,
        6: store_signed_byte,
        7: store_undefined,
        8: store_signed_short,
        9: store_signed_long,
        10: unknown,
        11: store_float,
        12: store_double,
        13: store_ifds
    }

    # dictionary API (sort of)

    def keys(self):
        return self.tagdata.keys()

    def items(self):
        return [(tag, self[tag]) for tag in self.tagdata.iterkeys()]

    def __len__(self):
        return len(self.tagdata)

    def __getitem__(self, tag):
        return self.load_dispatch[self.tagtype[tag]][1](self, self.tagdata[tag])

    def get(self, tag, default=None):
        try:
            return self[tag]
        except KeyError:
            return default

    def has_key(self, tag):
        return tag in self.tagdata

    def __contains__(self, tag):
        return tag in self.tagdata

    def __setitem__(self, tag, value):
        if not isinstance(value, tuple) and not isinstance(value, str):
            raise ValueError("Tag values must always be tuples or strings")

        if tag not in self.tagtype:
            raise KeyError("Tag type has not been set")


        self.tagdata[tag] = self.store_dispatch[self.tagtype[tag]](self, value)

    def load(self, fp):
        log.debug("ImageFileDirectory.load")

        # load tag dictionary
        self.reset()

        for i in xrange(self.i16(fp.read(2))):
            (tag, typ, n, data) = struct.unpack(self.endian + "HHL4s",
                fp.read(12))

            tagdesc = "tag: %s (%d) - type: %s (%d)" % (
                    tifftags.TAGS.get(tag, "UNKNOWN"), tag,
                    tifftags.TYPES.get(typ, "UNKNOWN"), typ
                )

            if typ not in self.load_dispatch:
                log.warning(tagdesc + " - unsupported type")
                continue # ignore unsupported type

            size = self.load_dispatch[typ][0] * n

            # Get and expand tag value
            if size > 4:
                here = fp.tell()
                fp.seek(struct.unpack(self.endian + "L", data)[0])
                data = fp.read(size)
                fp.seek(here)
            elif size < 4:
                data = data[:size]

            self.tagdata[tag] = data
            self.tagtype[tag] = typ

            if size > 64:
                log.debug(tagdesc + " - value: <%d bytes>" % size)
            else:
                log.debug(tagdesc + " - value: %s" % repr(self[tag]))

        # For some reason older versions of the the M9 firmware store XMP data
        # as undefined rather than byte -- fix that here.
        if XMP in self.tagtype:
            self.tagtype[XMP] = 1

        self.next = self.i32(fp.read(4))

    # save primitives
    def save(self, fp):
        log.debug("ImageFileDirectory.load")

        # always write in ascending tag order
        tags = list(self.tagdata.keys())
        tags.sort()

        directory = []

        fp.write(self.o16(len(tags)))
        offset = fp.tell() + len(tags) * 12 + 4

        stripoffsets = None

        # pass 1: convert tags to binary format
        for tag in tags:
            typ = self.tagtype[tag]
            data = self.tagdata[tag]
            size = len(data)
            n = len(data) / self.load_dispatch[typ][0]

            log.debug("save: %s (%d) - type %s (%d) - value: %s" % (
                    tifftags.TAGS.get(tag, "UNKNOWN"), tag,
                    tifftags.TYPES.get(typ, "UNKNOWN"), typ,
                    ("<table: %d bytes>" % size)
                        if size > 64 and typ in (1, 2, 7) else repr(self[tag])
                ))

            # figure out if data fits into the directory
            if size == 4:
                directory.append((tag, typ, n, data, ""))
            elif size < 4:
                directory.append((tag, typ, n, data + (4-size)*"\x00", ""))
            else:
                directory.append((tag, typ, n, self.o32(offset), data))
                offset += size
                if offset & 1:
                    offset = offset + 1 # word padding

        # pass 2: write directory to file
        for tag, typ, count, value, data in directory:
            fp.write(self.o16(tag) + self.o16(typ) + self.o32(count) + value)

        fp.write("\x00\x00\x00\x00") # end of directory

        # pass 3: write auxiliary data to file
        for tag, typ, count, value, data in directory:
            fp.write(data)
            if len(data) & 1:
                fp.write("\x00")

        return offset


class M9DNG(object):
    '''
    M9 DNGs are TIFF files containing the usual header, then:
    * SubIFD 1: Main image IFD
    * SubIFD 1: Main image IFD extended values
    * STRIP: Preview image IFD data (one strip)
    * STRIP: Main image data (one strip)
    * IFD 0: Preview image IFD (pointing to SubIFD 1)
    * IFD 0: Preview image IFD extended values
    * Exif IFD: pointed to by IFD 0

    We support updating tags etc in IFD 0, SubIFD 1, and EXIF -- image data
    is copied across verbatim.
    '''

    def __init__(self, fp):
        self.fp = fp

        # The M9 stores the preview in IFD0, and the main image in SubIFD1.
        self.main_ifd = None
        self.preview_ifd = None
        self.exif_ifd = None

        # Image data -- one strip per IFD, (offset, length) tuples
        self.main_strip = None
        self.preview_strip = None

        # Header
        log.debug("Reading header")
        ifh = self.fp.read(8)
        if ifh[:4] not in PREFIXES:
            raise SyntaxError("not a TIFF file")

        self.prefix = ifh[:2]
        if self.prefix == MM:
            self.o16, self.o32 = ob16, ob32
            self.i16, self.i32 = ib16, ib32
            self.endian = ">"
        elif self.prefix == II:
            self.o16, self.o32 = ol16, ol32
            self.i16, self.i32 = il16, il32
            self.endian = "<"
        else:
            raise SyntaxError("not a TIFF IFD")

        # Get pointer to preview IFD and skipt to start
        self.fp.seek(self.i32(ifh, 4))
        # Preview IFD is IFD 0
        log.debug("Loading preview IFD (0)")
        self.preview_ifd = ImageFileDirectory(self.prefix)
        self.preview_ifd.load(self.fp)

        # Get pointer to SubIFD 1
        log.debug("Loading main image IFD (SubIFD 1)")
        self.fp.seek(self.preview_ifd[SUBIFDS][0])
        self.main_ifd = ImageFileDirectory(self.prefix)
        self.main_ifd.load(self.fp)

        # Get pointer to Exif IFD
        log.debug("Loading EXIF IFD")
        self.fp.seek(self.preview_ifd[EXIFIFD][0])
        self.exif_ifd = ImageFileDirectory(self.prefix)
        self.exif_ifd.load(self.fp)

        # Get strip offset/length
        log.debug("Reading image data offsets")
        self.preview_strip = (self.preview_ifd[STRIPOFFSETS][0],
            self.preview_ifd[STRIPBYTECOUNTS][0])
        self.main_strip = (self.main_ifd[STRIPOFFSETS][0],
            self.main_ifd[STRIPBYTECOUNTS][0])


    def save(self, fp):
        log.debug("Calculating image data and EXIF IFD offsets")
        # Work out offset of first (preview) IFD; must be even
        dest_preview_offset = 8
        dest_main_offset = dest_preview_offset + self.preview_strip[1]
        dest_exififd_offset = dest_main_offset + self.main_strip[1]
        if dest_exififd_offset % 2:
            dest_exififd_offset += 1

        # Now that we have the offsets, update the relevant pointer tags
        self.preview_ifd[STRIPOFFSETS] = (dest_preview_offset, )
        self.preview_ifd[EXIFIFD] = (dest_exififd_offset, )
        self.main_ifd[STRIPOFFSETS] = (dest_main_offset, )

        # Write TIFF header -- leave offset to ifd0 blank as we'll fill it in
        # later
        log.debug("Writing header")
        fp.write(self.prefix + self.o16(42) + '\x00\x00\x00\x00')

        # Save preview data
        log.debug("Copying preview data")
        self.fp.seek(self.preview_strip[0])
        fp.seek(dest_preview_offset)
        fp.write(self.fp.read(self.preview_strip[1]))

        # Save main data
        log.debug("Copying main image data")
        self.fp.seek(self.main_strip[0])
        fp.seek(dest_main_offset)
        fp.write(self.fp.read(self.main_strip[1]))

        # Save EXIF IFD
        log.debug("Writing EXIF IFD")
        fp.seek(dest_exififd_offset)
        dest_subifd1_offset = self.exif_ifd.save(fp)
        if dest_subifd1_offset % 2:
            dest_subifd1_offset += 1

        # Save SubIFD 1
        log.debug("Saving main image IFD (SubIFD 1)")
        fp.seek(dest_subifd1_offset)
        dest_ifd0_offset = self.main_ifd.save(fp)
        if dest_ifd0_offset % 2:
            dest_ifd0_offset += 1

        # Update IFD 0 with the offset to main
        self.preview_ifd[SUBIFDS] = (dest_subifd1_offset, )

        # Save IFD 0
        log.debug("Saving preview IFD (0)")
        fp.seek(dest_ifd0_offset)
        self.preview_ifd.save(fp)

        # Update the header to point to IFD 0
        fp.seek(4)
        fp.write(self.o32(dest_ifd0_offset))

        try:
            fp.flush()
        except: pass
