"""Fake viewport and camera for the view tool tests. ``install`` puts them on ``adsk.core``."""
import copy

from . import core

_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x9a\xa0\xa0"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class ViewOrientations:
    FrontViewOrientation = 0
    BackViewOrientation = 1
    TopViewOrientation = 2
    BottomViewOrientation = 3
    LeftViewOrientation = 4
    RightViewOrientation = 5
    IsoTopRightViewOrientation = 6
    IsoTopLeftViewOrientation = 7
    IsoBottomRightViewOrientation = 8
    IsoBottomLeftViewOrientation = 9
    ArbitraryViewOrientation = 10


class CameraTypes:
    OrthographicCameraType = 0
    PerspectiveCameraType = 1
    PerspectiveWithOrthoFacesCameraType = 2


class Camera(core._Fake):
    def __init__(self):
        self.eye = core.Point3D(10.0, 0.0, 0.0)
        self.target = core.Point3D(0.0, 0.0, 0.0)
        self.upVector = core.Vector3D(0.0, 0.0, 1.0)
        self.cameraType = CameraTypes.OrthographicCameraType
        self.viewOrientation = ViewOrientations.ArbitraryViewOrientation
        self.perspectiveAngle = 0.6
        self.isSmoothTransition = True
        self.isFitView = False
        self.extents = (120.0, 80.0)
        self.extents_ok = True

    def getExtents(self):
        return (self.extents_ok, *self.extents)

    def setExtents(self, w, h):
        self.extents = (w, h)
        return self.extents_ok

    def copy(self):
        return copy.deepcopy(self)


class SaveImageFileOptions(core._Fake):
    def __init__(self, path):
        self.filename = path
        self.width = self.height = 0
        self.isBackgroundTransparent = False
        self.isAntiAliased = True

    @staticmethod
    def create(path):
        return SaveImageFileOptions(path)


class Viewport(core._Fake):
    """``camera`` returns a copy and the setter stores a copy; every set is recorded in ``camera_sets``."""

    def __init__(self, width=1600, height=1000):
        self.width, self.height = width, height
        self._camera = Camera()
        self.camera_sets: list[Camera] = []
        self.refreshes = 0
        self.fits = 0
        self.saves: list[tuple] = []  # ("plain", path, w, h) or ("options", path, w, h, transparent, aa)
        self.save_result = True
        self.save_exc = None
        self.write_file = True
        self.png = _PNG
        self.set_exc = None  # raised by the camera setter when set (after the first N sets)
        self.set_exc_after = 0

    @property
    def camera(self):
        return self._camera.copy()

    @camera.setter
    def camera(self, cam):
        if self.set_exc is not None and len(self.camera_sets) >= self.set_exc_after:
            raise self.set_exc
        self._camera = cam.copy()
        self.camera_sets.append(cam.copy())

    def refresh(self):
        self.refreshes += 1
        return True

    def fit(self):
        self.fits += 1
        self._camera.extents = (50.0, 50.0)
        self._camera.isFitView = True
        return True

    def _write(self, path):
        if self.save_exc is not None:
            raise self.save_exc
        if self.save_result and self.write_file:
            with open(path, "wb") as f:
                f.write(self.png)
        return self.save_result

    def saveAsImageFile(self, path, w, h):
        self.saves.append(("plain", path, w, h, self._camera.viewOrientation))
        return self._write(path)

    def saveAsImageFileWithOptions(self, o):
        self.saves.append(("options", o.filename, o.width, o.height, o.isBackgroundTransparent,
                           o.isAntiAliased, self._camera.viewOrientation))
        return self._write(o.filename)


def install(monkeypatch, with_options=True):
    """Add the view fakes to ``adsk.core`` for one test and give the fake app a viewport."""
    monkeypatch.setattr(core, "ViewOrientations", ViewOrientations, raising=False)
    monkeypatch.setattr(core, "CameraTypes", CameraTypes, raising=False)
    monkeypatch.setattr(core, "Viewport", Viewport, raising=False)
    monkeypatch.setattr(core, "Camera", Camera, raising=False)
    if with_options:
        monkeypatch.setattr(core, "SaveImageFileOptions", SaveImageFileOptions, raising=False)
    else:
        monkeypatch.delattr(core, "SaveImageFileOptions", raising=False)
    monkeypatch.setattr(core.Application, "activeViewport", None, raising=False)
