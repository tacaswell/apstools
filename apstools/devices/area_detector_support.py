"""
Area Detector Support
+++++++++++++++++++++++++++++++++++++++

.. autosummary::

   ~AD_EpicsFileNameHDF5Plugin
   ~AD_EpicsHdf5FileName
   ~AD_EpicsHDF5IterativeWriter
   ~AD_EpicsJpegFileName
   ~AD_full_file_name_local
   ~AD_plugin_primed
   ~AD_prime_plugin
   ~AD_prime_plugin2
   ~AD_setup_FrameType
"""

# TODO: JPEG & TIFF similar to AD_EpicsFileNameHDF5Plugin

from collections import OrderedDict
from ophyd.areadetector.filestore_mixins import FileStoreBase
from ophyd.areadetector.filestore_mixins import FileStoreIterativeWrite
from ophyd.areadetector.filestore_mixins import FileStorePluginBase
from ophyd.areadetector.plugins import HDF5Plugin_V34 as HDF5Plugin
from ophyd.areadetector.plugins import JPEGPlugin_V34 as JPEGPlugin
from ophyd.utils import set_and_wait
import datetime
import epics
import itertools
import logging
import numpy as np
import pathlib
import time
import warnings

logger = logging.getLogger(__name__)

# fmt: off
AD_FrameType_schemes = {
    "reset": dict(  # default names from Area Detector code
        ZRST="Normal",
        ONST="Background",
        TWST="FlatField",
    ),
    "NeXus": dict(  # NeXus (typical locations)
        ZRST="/entry/data/data",
        ONST="/entry/data/dark",
        TWST="/entry/data/white",
    ),
    "DataExchange": dict(  # APS Data Exchange
        ZRST="/exchange/data",
        ONST="/exchange/data_dark",
        TWST="/exchange/data_white",
    ),
}
# fmt: on


def AD_setup_FrameType(prefix, scheme="NeXus"):
    """
    configure so frames are identified & handled by type (dark, white, or image)

    PARAMETERS

        prefix
            *str* :
            EPICS PV prefix of area detector, such as ``13SIM1:``
        scheme
            *str* :
            any key in the ``AD_FrameType_schemes`` dictionary

    This routine prepares the EPICS Area Detector to identify frames
    by image type for handling by clients, such as the HDF5 file writing plugin.
    With the HDF5 plugin, the ``FrameType`` PV is added to the NDattributes
    and then used in the layout file to direct the acquired frame to
    the chosen dataset.  The ``FrameType`` PV value provides the HDF5 address
    to be used.

    To use a different scheme than the defaults, add a new key to
    the ``AD_FrameType_schemes`` dictionary, defining storage values for the
    fields of the EPICS ``mbbo`` record that you will be using.

    see: https://nbviewer.jupyter.org/github/BCDA-APS/use_bluesky/blob/main/lessons/sandbox/images_darks_flats.ipynb

    EXAMPLE::

        AD_setup_FrameType("2bmbPG3:", scheme="DataExchange")

    * Call this function *before* creating the ophyd area detector object
    * use lower-level PyEpics interface
    """
    db = AD_FrameType_schemes.get(scheme)
    if db is None:
        raise ValueError(
            f"unknown AD_FrameType_schemes scheme: {scheme}"
            "\n Should be one of: " + ", ".join(AD_FrameType_schemes.keys())
        )

    template = "{}cam1:FrameType{}.{}"
    for field, value in db.items():
        epics.caput(template.format(prefix, "", field), value)
        epics.caput(template.format(prefix, "_RBV", field), value)


def AD_plugin_primed(plugin):
    """
    Has area detector pushed an NDarray to the file writer plugin?  True or False

    PARAMETERS

    plugin
        *obj* :
        area detector plugin to be *primed* (such as ``detector.hdf1``)

    EXAMPLE::

        AD_plugin_primed(detector.hdf1)

    Works around an observed issue: #598
    https://github.com/NSLS-II/ophyd/issues/598#issuecomment-414311372

    If detector IOC has just been started and has not yet taken an image
    with the file writer plugin, then a TimeoutError will occur as the
    file writer plugin "Capture" is set to 1 (Start).  In such case,
    first acquire at least one image with the file writer plugin enabled.

    Also issue in apstools (needs a robust method to detect if primed):
    https://github.com/BCDA-APS/apstools/issues/464

    Since Area Detector release 2.1 (2014-10-14).

    The *prime* process is not needed if you select the
    *LazyOpen* feature with *Stream* mode for the file plugin.
    *LazyOpen* defers file creation until the first frame arrives
    in the plugin. This removes the need to initialize the plugin
    with a dummy frame before starting capture.
    """
    cam = plugin.parent.cam
    tests = []

    for obj in (cam, plugin):
        test = np.array(obj.array_size.get()).sum() != 0
        tests.append(test)
        if not test:
            logger.debug("'%s' image size is zero", obj.name)

    checks = dict(
        array_size=False,
        color_mode=True,
        data_type=True,
    )
    for key, as_string in checks.items():
        c = getattr(cam, key).get(as_string=as_string)
        p = getattr(plugin, key).get(as_string=as_string)
        test = c == p
        tests.append(test)
        if not test:
            logger.debug("%s does not match", key)

    return False not in tests


def AD_prime_plugin(detector, plugin):
    """
    Prime this area detector's file writer plugin.

    PARAMETERS

    detector
        *obj* :
        area detector (such as ``detector``)
    plugin
        *obj* :
        area detector plugin to be *primed* (such as ``detector.hdf1``)

    EXAMPLE::

        AD_prime_plugin(detector, detector.hdf1)
    """
    nm = f"{plugin.parent.name}.{plugin.attr_name}"
    warnings.warn(f"Use AD_prime_plugin2({nm}) instead.")
    AD_prime_plugin2(plugin)


def AD_prime_plugin2(plugin):
    """
    Prime this area detector's file writer plugin.

    Collect and push an NDarray to the file writer plugin.
    Works with all file writer plugins.

    Based on ``ophyd.areadetector.plugins.HDF5Plugin.warmup()``.

    PARAMETERS

    plugin
        *obj* :
        area detector plugin to be *primed* (such as ``detector.hdf1``)

    EXAMPLE::

        AD_prime_plugin2(detector.hdf1)

    """
    if AD_plugin_primed(plugin):
        logger.debug("'%s' plugin is already primed", plugin.name)
        return

    sigs = OrderedDict(
        [
            (plugin.enable, 1),
            (plugin.parent.cam.array_callbacks, 1),  # set by number
            (plugin.parent.cam.image_mode, 0),  # Single, set by number
            # Trigger mode names are not identical for every camera.
            # Assume here that the first item in the list is
            # the best default choice to prime the plugin.
            (plugin.parent.cam.trigger_mode, 0),  # set by number
            # just in case the acquisition time is set very long...
            (plugin.parent.cam.acquire_time, 1),
            (plugin.parent.cam.acquire_period, 1),
            (plugin.parent.cam.acquire, 1),  # set by number
        ]
    )

    original_vals = {sig: sig.get() for sig in sigs}

    for sig, val in sigs.items():
        time.sleep(0.1)  # abundance of caution
        set_and_wait(sig, val)

    time.sleep(2)  # wait for acquisition

    for sig, val in reversed(list(original_vals.items())):
        time.sleep(0.1)
        set_and_wait(sig, val)


def AD_full_file_name_local(plugin):
    """
    Return AD plugin's *Last filename* using local filesystem path.

    Get the full name, in terms of the bluesky filesystem, for the image file
    recently-acquired by the area detector plugin.

    Return the name as a pathlib object.

    PARAMETERS

    plugin *obj* :
        Instance of ophyd area detector file writing plugin.

    (new in apstools release 1.6.2)
    """
    fname = plugin.full_file_name.get().strip()
    if fname == "":
        return None

    ffname = pathlib.Path(fname)  # FIXME: OS style?
    if plugin.read_path_template == plugin.write_path_template:
        return ffname

    # identify the common last parts of the file directories
    rparts = pathlib.Path(plugin.read_path_template).parts
    wparts = pathlib.Path(plugin.write_path_template).parts
    icommon = 0
    for i in range(min(len(rparts), len(wparts))):
        i1 = -i - 1
        if rparts[i1:] != wparts[i1:]:
            icommon = i
            break

    if icommon == 0:
        raise ValueError(
            "No common part to file paths. " "Cannot convert to local file path."
        )

    # fmt: off
    local_root = pathlib.Path().joinpath(*rparts[:-icommon])
    common_parts = ffname.parts[len(wparts[:-icommon]):]
    local_ffname = local_root.joinpath(*common_parts)
    # fmt: on

    return local_ffname


class AD_EpicsHdf5FileName(FileStorePluginBase):
    """
    Custom class to define image file name from EPICS.

    Used as part of AD_EpicsFileNameHDF5Plugin.

    .. index:: Ophyd Device Support; AD_EpicsHdf5FileName

    .. caution:: *Caveat emptor* applies here.  You assume expertise!

    Replace standard Bluesky algorithm where file names
    are defined as UUID strings, virtually guaranteeing that
    no existing images files will ever be overwritten.

    .. autosummary::

        ~make_filename
        ~get_frames_per_point
        ~stage

    To allow users to control the file **name**,
    we override the ``make_filename()`` method here
    and we need to override some intervening classes.

    To allow users to control the file **number**,
    we override the ``stage()`` method here
    and triple-comment out that line, and bring in
    sections from the methods we are replacing here.

    It is allowed to set the ``file_template="%s%s.h5"``
    so the file name does not include the file number.

    The image file name is set in ``FileStoreBase.make_filename()``
    from ``ophyd.areadetector.filestore_mixins``.  This is called
    (during device staging) from ``FileStoreBase.stage()``
    """

    def __init__(self, *args, **kwargs):
        FileStorePluginBase.__init__(self, *args, **kwargs)
        self.filestore_spec = "AD_HDF5"  # spec name stored in resource doc
        self.stage_sigs.update(
            [
                ("create_directory", -5),
                ("file_template", "%s%s_%4.4d.h5"),
                ("file_write_mode", "Stream"),
                ("capture", 1),
            ]
        )
        # "capture" must always come last
        self.stage_sigs["capture"] = self.stage_sigs.pop("capture")

    def make_filename(self):
        """
        overrides default behavior: Get info from EPICS HDF5 plugin.
        """
        # start of the file name, file number will be appended per template
        filename = self.file_name.get()
        file_path = self.file_path.get()
        formatter = datetime.datetime.now().strftime

        # Directory where the HDF5 plugin will write the
        # image, using the IOC's filesystem.
        # write_path = formatter(self.write_path_template)
        write_path = formatter(file_path)

        # this is where the DataBroker will find the image,
        # on a filesystem accessible to Bluesky
        read_path = formatter(self.read_path_template)

        return filename, read_path, write_path

    def get_frames_per_point(self):
        """overrides default behavior"""
        return self.num_capture.get()

    def stage(self):
        """
        overrides default behavior

        Set EPICS items before device is staged, then copy EPICS
        naming template (and other items) to ophyd after staging.
        """
        # Make a filename.
        filename, read_path, write_path = self.make_filename()

        # Ensure we do not have an old file open.
        set_and_wait(self.capture, 0)
        # These must be set before parent is staged (specifically
        # before capture mode is turned on. They will not be reset
        # on 'unstage' anyway.
        set_and_wait(self.file_path, write_path)
        set_and_wait(self.file_name, filename)
        # set_and_wait(self.file_number, 0)

        # get file number now since it is incremented during stage()
        file_number = self.file_number.get()
        # Must avoid parent's stage() since it sets file_number to 0
        # Want to call grandparent's stage()
        # super().stage()     # avoid this - sets `file_number` to zero
        # call grandparent.stage()
        FileStoreBase.stage(self)

        # AD does the file name templating in C
        # We can't access that result until after acquisition
        # so we apply the same template here in Python.
        template = self.file_template.get()
        try:
            self._fn = template % (read_path, filename, file_number)
        except TypeError:
            # in case template does not include file_number
            self._fn = template % (read_path, filename)
        self._fp = read_path
        if not self.file_path_exists.get():
            raise IOError(f"Path '{self.file_path.get()}' does not exist on IOC.")

        self._point_counter = itertools.count()

        # from FileStoreHDF5.stage()
        res_kwargs = {"frame_per_point": self.get_frames_per_point()}
        self._generate_resource(res_kwargs)


class AD_EpicsHDF5IterativeWriter(AD_EpicsHdf5FileName, FileStoreIterativeWrite):
    """
    intermediate class between AD_EpicsHdf5FileName and AD_EpicsFileNameHDF5Plugin

    (new in apstools release 1.6.2)
    """

    pass


class AD_EpicsFileNameHDF5Plugin(HDF5Plugin, AD_EpicsHDF5IterativeWriter):
    """
    Alternative to HDF5Plugin: EPICS area detector PV sets file name.

    Uses ``AD_EpicsHdf5FileName``.

    EXAMPLE::

        from apstools.devices.area_detector_support import AD_EpicsFileNameHDF5Plugin
        from ophyd import EpicsSignalWithRBV
        from ophyd.areadetector import ADComponent
        from ophyd.areadetector import DetectorBase
        from ophyd.areadetector import SingleTrigger
        from ophyd.areadetector.plugins import ImagePlugin_V34 as ImagePlugin
        from ophyd.areadetector.plugins import PvaPlugin_V34 as PvaPlugin
        from ophyd.areadetector import SimDetectorCam
        import datetime
        import pathlib


        IOC = "ad:"
        IMAGE_DIR = "adsimdet/%Y/%m/%d"
        AD_IOC_MOUNT_PATH = pathlib.Path("/tmp")
        BLUESKY_MOUNT_PATH = pathlib.Path("/tmp/docker_ioc/iocad/tmp")

        # MUST end with a `/`, pathlib will NOT provide it
        WRITE_PATH_TEMPLATE = f"{AD_IOC_MOUNT_PATH / IMAGE_DIR}/"
        READ_PATH_TEMPLATE = f"{BLUESKY_MOUNT_PATH / IMAGE_DIR}/"


        class MyFixedCam(SimDetectorCam):
            pool_max_buffers = None
            offset = ADComponent(EpicsSignalWithRBV, "Offset")


        class MySimDetector(SingleTrigger, DetectorBase):
            '''ADSimDetector'''

            cam = ADComponent(MyFixedCam, "cam1:")
            image = ADComponent(ImagePlugin, "image1:")
            hdf1 = ADComponent(
                AD_EpicsFileNameHDF5Plugin,
                "HDF1:",
                write_path_template=WRITE_PATH_TEMPLATE,
                read_path_template=READ_PATH_TEMPLATE,
            )
            pva = ADComponent(PvaPlugin, "Pva1:")

    (new in apstools release 1.6.2)
    """

    pass


class AD_EpicsJpegFileName(FileStorePluginBase):

    """
    custom class to define image file name from EPICS

    .. index:: Ophyd Device Support; AD_EpicsJpegFileName

    .. caution:: *Caveat emptor* applies here.  You assume expertise!

    Replace standard Bluesky algorithm where file names
    are defined as UUID strings, virtually guaranteeing that
    no existing images files will ever be overwritten.
    Also, this method decouples the data files from the databroker,
    which needs the files to be named by UUID.

    .. autosummary::
        ~make_filename
        ~generate_datum
        ~get_frames_per_point
        ~stage

    Patterned on ``apstools.devices.AD_EpicsHdf5FileName()``.
    (Follow that documentation from this point.)
    """

    def __init__(self, *args, **kwargs):
        FileStorePluginBase.__init__(self, *args, **kwargs)
        # TODO: taking a guess here, it's needed if databroker is to *read* the image file
        # If we get this wrong, we have to update any existing runs before
        # databroker can read them into memory.  If there is not such symbol
        # defined, it's up to somone who wants to read these images with databroker.
        self.filestore_spec = "AD_JPEG"  # spec name stored in resource doc
        self.stage_sigs.update(
            [
                ("file_template", "%s%s_%4.4d.jpg"),
                ("file_write_mode", "Stream"),
                ("capture", 1),
            ]
        )

    def make_filename(self):
        """
        overrides default behavior: Get info from EPICS JPEG plugin.
        """
        # start of the file name, file number will be appended per template
        filename = self.file_name.get()

        # this is where the JPEG plugin will write the image,
        # relative to the IOC's filesystem
        write_path = self.file_path.get()

        # this is where the DataBroker will find the image,
        # on a filesystem accessible to Bluesky
        read_path = write_path

        return filename, read_path, write_path

    def generate_datum(self, key, timestamp, datum_kwargs):
        """Generate a uid and cache it with its key for later insertion."""
        template = self.file_template.get()
        filename, read_path, write_path = self.make_filename()
        file_number = self.file_number.get()
        jpeg_file_name = template % (read_path, filename, file_number)

        # inject the actual name of the JPEG file here into datum_kwargs
        datum_kwargs["JPEG_file_name"] = jpeg_file_name

        logger.debug("make_filename: %s", jpeg_file_name)
        logger.debug("write_path: %s", write_path)
        return super().generate_datum(key, timestamp, datum_kwargs)

    def get_frames_per_point(self):
        """overrides default behavior"""
        return self.num_capture.get()

    def stage(self):
        """
        overrides default behavior

        Set EPICS items before device is staged, then copy EPICS
        naming template (and other items) to ophyd after staging.
        """
        # Make a filename.
        filename, read_path, write_path = self.make_filename()

        # Ensure we do not have an old file open.
        set_and_wait(self.capture, 0)
        # These must be set before parent is staged (specifically
        # before capture mode is turned on. They will not be reset
        # on 'unstage' anyway.
        set_and_wait(self.file_path, write_path)
        set_and_wait(self.file_name, filename)
        # set_and_wait(self.file_number, 0)

        # get file number now since it is incremented during stage()
        file_number = self.file_number.get()
        # Must avoid parent's stage() since it sets file_number to 0
        # Want to call grandparent's stage()
        # super().stage()     # avoid this - sets `file_number` to zero
        # call grandparent.stage()
        FileStoreBase.stage(self)

        # AD does the file name templating in C
        # We can't access that result until after acquisition
        # so we apply the same template here in Python.
        template = self.file_template.get()
        try:
            self._fn = template % (read_path, filename, file_number)
        except TypeError:
            # in case template does not include file_number
            self._fn = template % (read_path, filename)
        self._fp = read_path
        if not self.file_path_exists.get():
            raise IOError("Path {} does not exist on IOC.".format(self.file_path.get()))

        self._point_counter = itertools.count()

        # from FileStoreHDF5.stage()
        res_kwargs = {"frame_per_point": self.get_frames_per_point()}
        self._generate_resource(res_kwargs)


# -----------------------------------------------------------------------------
# :author:    Pete R. Jemian
# :email:     jemian@anl.gov
# :copyright: (c) 2017-2022, UChicago Argonne, LLC
#
# Distributed under the terms of the Creative Commons Attribution 4.0 International Public License.
#
# The full license is in the file LICENSE.txt, distributed with this software.
# -----------------------------------------------------------------------------
