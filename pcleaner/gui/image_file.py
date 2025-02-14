import io
import zlib
import json
import attrs
from attrs import fields
from pathlib import Path
from typing import Iterable
from enum import Enum, auto

import PySide6.QtGui as Qg
from PIL import Image

import pcleaner.config as cfg


# The max size used for the icon and large thumbnail.
THUMBNAIL_SIZE = 44, 44


class ProcessStep:
    """
    This class represents a step in the image processing pipeline.
    The profile_sensitivity attribute is a list of profile entries that affect the processing
    that takes place in this step.
    The format is a list of attrs attributes.
    For example: [fields(Profile).general, fields(Profile).denoiser.filter_strength]
    A special case: None means that all profile entries affect this step.
    """

    path: Path | None = None
    description: str
    _sensitivity_filter: attrs.filters
    _current_profile_checksum: int | None = None

    def __init__(
        self, description: str, profile_sensitivity: Iterable[attrs.Attribute] | None = None
    ):
        self.description = description
        if profile_sensitivity is None:
            self._sensitivity_filter = None
        else:
            self._sensitivity_filter = attrs.filters.include(*profile_sensitivity)

    def has_path(self) -> bool:
        return self.path is not None

    def update_checksum(self, profile: cfg.Profile) -> None:
        """
        Update the checksum of the profile entries that this step is sensitive to.

        :param profile: The profile to calculate the checksum for.
        """
        self._current_profile_checksum = self._profile_checksum(profile)

    def is_profile_changed(self, profile: cfg.Profile) -> bool:
        """
        Check if the profile entries that this step is sensitive to have changed.

        :param profile: The profile to check.
        :return: True if the profile has changed, False otherwise.
        """
        return self._current_profile_checksum != self._profile_checksum(profile)

    def _profile_checksum(self, profile: cfg.Profile) -> int:
        """
        Calculate a checksum of the profile entries that this step is sensitive to.

        :param profile: The profile to calculate the checksum for.
        :return: The checksum.
        """
        serialized_data = json.dumps(
            attrs.asdict(profile, filter=self._sensitivity_filter), sort_keys=True
        ).encode()
        return zlib.crc32(serialized_data)


class Step(Enum):
    input = auto()
    ai_mask = auto()
    initial_boxes = auto()
    final_boxes = auto()
    box_mask = auto()
    cut_mask = auto()
    mask_layers = auto()
    final_mask = auto()
    mask_overlay = auto()
    masked_image = auto()
    denoiser_mask = auto()
    denoised_image = auto()


class ImageFile:
    """
    This class represents an image file.
    """

    path: Path  # Path to the image file.
    icon: Qg.QIcon  # Placeholder icon for the image type.
    # The following attributes are lazy-loaded.
    thumbnail: Qg.QPixmap | None = None  # Thumbnail of the image, used as the icon.
    size: tuple[int, int] | None = None  # Size of the image.
    steps: dict[Step, ProcessStep]  # Map of steps to ProcessStep objects.

    error: Exception | None = None  # Error that occurred during any process.

    def __init__(self, path: Path):
        """
        Init the image file.

        :param path: Path to the image file.
        """
        self.path = path
        self.icon = Qg.QIcon.fromTheme(cfg.SUFFIX_TO_ICON[path.suffix.lower()])

        pro = fields(cfg.Profile)
        gen = fields(cfg.GeneralConfig)
        td = fields(cfg.TextDetectorConfig)
        pp = fields(cfg.PreprocessorConfig)
        mk = fields(cfg.MaskerConfig)
        # dn = fields(cfg.DenoiserConfig)

        # Init the process steps.
        # Here I need to account for all the settings that affect each step.
        settings = [gen.input_size_scale]
        self.steps[Step.input] = ProcessStep(
            "The original image with the scale factor applied.", settings
        )

        settings += [td.model_path]
        self.steps[Step.ai_mask] = ProcessStep("The rough mask generated by the AI.", settings)

        settings += [
            pp.box_min_size,
            pp.suspicious_box_min_size,
            pp.box_padding_initial,
            pp.box_right_padding_initial,
        ]
        self.steps[Step.initial_boxes] = ProcessStep(
            "The outlines of the text boxes the AI found.", settings
        )

        settings += [pro.preprocessor]
        self.steps[Step.final_boxes] = ProcessStep(
            "The final boxes after expanding, merging and filtering unneeded boxes with OCR.\n"
            "Green: initial boxes. Red: extended boxes. Purple: merged (final) boxes. "
            "Blue: reference boxes for denoising.",
            settings,
        )
        self.steps[Step.box_mask] = ProcessStep("The mask of the merged boxes.", settings)
        self.steps[Step.cut_mask] = ProcessStep(
            "The rough text detection mask with everything outside the box mask cut out.", settings
        )

        settings += [mk.mask_growth_step_pixels, mk.mask_growth_steps]
        self.steps[Step.mask_layers] = ProcessStep(
            "The different steps of growth around the cut mask displayed in different colors.",
            settings,
        )

        settings += [
            mk.off_white_max_threshold,
            mk.mask_improvement_threshold,
            mk.mask_selection_fast,
            mk.mask_max_standard_deviation,
        ]
        self.steps[Step.final_mask] = ProcessStep(
            "The collection of masks for each bubble that fit best.", settings
        )

        self.steps[Step.mask_overlay] = ProcessStep(
            "The input image with the final mask overlaid in color.",
            settings + [mk.debug_mask_color],
        )
        self.steps[Step.masked_image] = ProcessStep(
            "The input image with the final mask applied.", settings
        )

        settings += [pro.denoiser]
        self.steps[Step.denoiser_mask] = ProcessStep(
            "The final mask overlaid on a denoised portion of the input image.", settings
        )
        self.steps[Step.denoised_image] = ProcessStep(
            "The input image with the denoised mask applied.", settings
        )

    @property
    def size_str(self) -> str:
        """
        Returns the size of the image as a string.
        """
        if self.size is None:
            return "Unknown"
        return f"{self.size[0]:n} × {self.size[1]:n}"

    def data_loaded(self) -> bool:
        """
        Returns whether the lazily-loaded image data is ready.
        """
        return self.thumbnail is not None and self.size is not None

    def load_image(self) -> Path:
        """
        Loads the image data.

        :return: Path to the image file so that the callback knows which image was loaded.
        """
        image = Image.open(self.path)
        self.size = image.size
        # Shrink the image down to a thumbnail size to reduce memory usage.
        thumbnail = image.copy()
        thumbnail.thumbnail(THUMBNAIL_SIZE)
        self.thumbnail = convert_PIL_to_QPixmap(thumbnail)

        return self.path


def convert_PIL_to_QPixmap(image: Image.Image) -> Qg.QPixmap:
    """
    Converts a PIL image to a QPixmap.

    :param image: PIL image.
    :return: QPixmap.
    """
    if image.mode == "CMYK":
        image = image.convert("RGB")
    byte_data = io.BytesIO()
    image.save(byte_data, format="PNG")
    byte_data.seek(0)
    qimage = Qg.QPixmap()
    # noinspection PyTypeChecker
    qimage.loadFromData(byte_data.read(), "PNG")
    return qimage
