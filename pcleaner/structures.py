import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from importlib import resources
from pcleaner import data

import magic
from PIL import Image, ImageDraw, ImageFont

import pcleaner.config as cfg


class BoxType(Enum):
    BOX = 0
    EXTENDED_BOX = 1
    MERGED_EXT_BOX = 2
    REFERENCE_BOX = 3


@dataclass
class PageData:
    """
    This dataclass represents the json data generated by the ai model.
    It contains the image path, mask path, the boxes, the extended boxes,
    and the merged extended boxes.

    - boxes: The boxes generated by the ai model, only expanded slightly for extra padding.
      These are used to make a tight-fitting mask.
    - extended_boxes: The boxes expanded a lot and used for masking off potential false positives.
    - merged_extended_boxes: Overlapping extended boxes are merged into one box, to prevent
      conflicts of overlapping mask regions. The original extended boxes are still kept,
      to provide a more precise mask for the initial cut. These are used to cut out the mask when
      analyzing the fit.
    - reference_boxes: These are extensions of the merged extended boxes, to make sure the mask
      has room to grow in the base image when providing analysis. These are used to cut out the
      base image when analyzing the fit.

    Boxes are represented as tuples of (x1, y1, x2, y2), where (x1, y1) is the top left corner,
    and (x2, y2) is the bottom right corner.
    """

    image_path: str  # Path to the copied png.
    mask_path: str  # Path to the generated mask.png
    original_path: str  # Path to the original image. (used for relative output)
    scale: float  # The size of the original image relative to the png.
    boxes: list[tuple[int, int, int, int]]
    extended_boxes: list[tuple[int, int, int, int]]
    merged_extended_boxes: list[tuple[int, int, int, int]]
    reference_boxes: list[tuple[int, int, int, int]]
    _image_size: tuple[
        int, int
    ] = None  # Cache the image size, so we don't have to load the image every time.

    @classmethod
    def from_json(cls, json_str: str) -> "PageData":
        """
        Load a previously dumped PageData object from a json file.

        :param json_str: The json string to load from.
        """
        json_data = json.loads(json_str)
        return cls(
            json_data["image_path"],
            json_data["mask_path"],
            json_data["original_path"],
            json_data["scale"],
            json_data["boxes"],
            json_data["extended_boxes"],
            json_data["merged_extended_boxes"],
            json_data["reference_boxes"],
        )

    @property
    def image_size(self):
        if self._image_size is None:
            metadata = magic.from_file(self.image_path)
            size_str = re.search(r"(\d+) x (\d+)", metadata).groups()
            self._image_size = (int(size_str[0]), int(size_str[1]))
        return self._image_size

    def boxes_from_type(self, box_type: BoxType) -> list[tuple[int, int, int, int]]:
        match box_type:
            case BoxType.BOX:
                return self.boxes
            case BoxType.EXTENDED_BOX:
                return self.extended_boxes
            case BoxType.MERGED_EXT_BOX:
                return self.merged_extended_boxes
            case BoxType.REFERENCE_BOX:
                return self.reference_boxes
            case _:
                raise ValueError("Invalid box type.")

    def grow_boxes(self, padding: int, box_type: BoxType):
        """
        Uniformly grow all boxes by padding pixels.
        Checks that the boxes don't go out of bounds.

        :param padding: number of pixels to grow each box by.
        :param box_type: type of box to grow.
        """
        boxes = self.boxes_from_type(box_type)
        image_width, image_height = self.image_size

        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i]
            boxes[i] = (
                max(x1 - padding, 0),
                max(y1 - padding, 0),
                min(x2 + padding, image_width),
                min(y2 + padding, image_height),
            )

    def right_pad_boxes(self, padding: int, box_type: BoxType):
        """
        Right-pad all boxes by padding pixels.
        Checks that the boxes don't go out of bounds.

        :param padding: number of pixels to right-pad each box by.
        :param box_type: type of box to right-pad.
        """
        boxes = self.boxes_from_type(box_type)
        image_width, _ = self.image_size

        for i in range(len(boxes)):
            x1, y1, x2, y2 = boxes[i]
            boxes[i] = (x1, y1, min(x2 + padding, image_width), y2)

    @staticmethod
    def box_size(box: tuple[int, int, int, int]) -> int:
        """
        Calculate the size of a box.

        :param box: The box to calculate the size of.
        :return: The size of the box.
        """
        return (box[2] - box[0]) * (box[3] - box[1])

    def visualize(self, image_path: Path, out_dir: Path | None = None, final_boxes: bool = False):
        """
        Visualize the boxes on an image.
        Typically, this would be used to check where on the original image the
        boxes are located.

        :param image_path: The path to the image to visualize the boxes on.
        :param out_dir: [Optional] The directory to save the visualization to.
            By default, the visualization is saved to the same directory as the image.
        :param final_boxes: [Optional] Whether this visualization is after OCR.
        """
        image = Image.open(image_path)
        draw = ImageDraw.Draw(image)
        with resources.files(data) as data_path:
            font_path = str(data_path / "LiberationSans-Regular.ttf")
        # Figure out the optimal font size based on the image size. E.g. 30 for a 1600px image.
        font_size = int(image.size[0] / 50)

        for index, box in enumerate(self.boxes):
            draw.rectangle(box, outline="green")
            # Draw the box number, with a white background, respecting font size.
            draw.rectangle(
                (box[2] - font_size, box[1] + 2, box[2] - 2, box[1] + font_size),
                fill="white",
                outline="white",
            )
            draw.text(
                (box[2] - font_size, box[1]),
                str(index + 1),
                fill="green",
                font=ImageFont.truetype(font_path, font_size),
                direction="rtl",
            )

        for box in self.extended_boxes:
            draw.rectangle(box, outline="red")
        for box in self.merged_extended_boxes:
            draw.rectangle(box, outline="purple")
        for box in self.reference_boxes:
            draw.rectangle(box, outline="blue")
        # Save the image.
        extension = "_boxes" if not final_boxes else "_boxes_final"
        out_path = image_path.with_stem(image_path.stem + extension)
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / image_path.name
        image.save(out_path)

    def make_box_mask(self, image_size: tuple[int, int], box_type: BoxType) -> Image:
        """
        Draw the boxes as a bitmap mask, where 1 represents box, and 0 represents no box.
        This is in essence just an image where the color is either black or white.

        :param image_size: The size of the image to make the mask for.
        :param box_type: The type of box to use.
        :return: The mask. Image mode: "1"
        """
        box_mask = Image.new("1", image_size, (0,))
        draw = ImageDraw.Draw(box_mask)
        boxes = self.boxes_from_type(box_type)
        for box in boxes:
            draw.rectangle(box, fill=(1,))
        return box_mask

    def resolve_overlaps(self):
        """
        Copy the extended boxes to the merged extended boxes, and merge overlapping boxes.
        """

        def boxes_overlap(box1: tuple[int, int, int, int], box2: tuple[int, int, int, int]) -> bool:
            x1_min, y1_min, x1_max, y1_max = box1
            x2_min, y2_min, x2_max, y2_max = box2

            x_overlap = (x1_min <= x2_max) and (x2_min <= x1_max)
            y_overlap = (y1_min <= y2_max) and (y2_min <= y1_max)

            # If both dimensions overlap, then the boxes overlap.
            return x_overlap and y_overlap

        def merge_boxes(
            box1: tuple[int, int, int, int], box2: tuple[int, int, int, int]
        ) -> tuple[int, int, int, int]:
            x1_min, y1_min, x1_max, y1_max = box1
            x2_min, y2_min, x2_max, y2_max = box2

            x_min = min(x1_min, x2_min)
            y_min = min(y1_min, y2_min)
            x_max = max(x1_max, x2_max)
            y_max = max(y1_max, y2_max)

            return x_min, y_min, x_max, y_max

        # Place the extended boxes in the merged extended boxes, merging overlapping boxes.
        merge_queue = set(self.extended_boxes)
        merged_boxes = []
        while merge_queue:
            box = merge_queue.pop()
            # Find all boxes that overlap with this box.
            overlapping_boxes = [b for b in merge_queue if boxes_overlap(box, b)]
            # Merge all overlapping boxes.
            for b in overlapping_boxes:
                box = merge_boxes(box, b)
                merge_queue.remove(b)
            merged_boxes.append(box)

        self.merged_extended_boxes = merged_boxes


@dataclass
class MaskFittingResults:
    """
    This is a simple struct to hold the results from the mask fitting process.
    Since it returns a lot of data, this is more readable.
    """

    best_mask: Image
    median_color: int
    mask_coords: tuple[int, int]
    analytics_page_path: Path
    analytics_std_deviation: float
    analytics_mask_index: int
    mask_box: tuple[int, int, int, int]  # Used for the denoising process.
    debug_masks: list[Image]

    @property
    def analytics(self) -> tuple[Path, bool, int, float]:
        return (
            self.analytics_page_path,
            self.best_mask is not None,
            self.analytics_mask_index,
            self.analytics_std_deviation,
        )

    @property
    def failed(self) -> bool:
        return self.best_mask is None

    @property
    def mask_data(self) -> tuple[Image, int, tuple[int, int]]:
        return self.best_mask, self.median_color, self.mask_coords

    @property
    def noise_mask_data(self) -> tuple[tuple[int, int, int, int], float]:
        return self.mask_box, self.analytics_std_deviation


@dataclass
class MaskerData:
    """
    This is a simple struct to hold the inputs for the masker.
    The data is a tuple of:
    - The json file path.
    - The image output directory. When none, output to the cache dir for denoising to continue.
    - The image cache directory.
    - The general config.
    - The masker config.
    - The save only mask flag.
    - The save only cleaned flag.
    - The save only text flag.
    - The extract text flag.
    - The show masks flag. (when true, save intermediate masks to the cache directory)
    - The debug flag.
    """

    json_path: Path
    output_dir: Path | None
    cache_dir: Path
    general_config: cfg.GeneralConfig
    masker_config: cfg.MaskerConfig
    save_only_mask: bool
    save_only_cleaned: bool
    save_only_text: bool
    extract_text: bool
    show_masks: bool
    debug: bool


@dataclass
class MaskData:
    """
    This is a simple struct to hold all the extra information needed to perform the
    denoising process.

    - The original image path.
    - The target image path.
    - The cleaned image path.
    - The mask image path.
    - The scale of the original image to the base image.
    - The box coordinates with their respective standard deviation for the masks.
    """

    original_path: Path
    target_path: Path
    base_image_path: Path
    mask_path: Path
    scale: float
    boxes_with_deviation: list[tuple[tuple[int, int, int, int], float]]

    @classmethod
    def from_json(cls, json_str: str) -> "MaskData":
        """
        Create a MaskData object from a json string.

        :param json_str: The json string.
        :return: The MaskData object.
        """
        data = json.loads(json_str)
        return cls(
            Path(data["original_path"]),
            Path(data["target_path"]),
            Path(data["base_image_path"]),
            Path(data["mask_path"]),
            data["scale"],
            data["boxes_with_deviation"],
        )

    def write_json(self, json_path: Path):
        """
        Write the MaskData object to a json file.

        :param json_path: The path to write the json file to.
        """
        # Convert the Path objects to strings.
        data = {
            "original_path": str(self.original_path),
            "target_path": str(self.target_path),
            "base_image_path": str(self.base_image_path),
            "mask_path": str(self.mask_path),
            "scale": self.scale,
            "boxes_with_deviation": self.boxes_with_deviation,
        }
        with open(json_path, "w") as f:
            json.dump(data, f, indent=4)


@dataclass
class DenoiserData:
    """
    This is a simple struct to hold the inputs for the denoiser.
    The data is a tuple of:
    - The json file path.
    - The image output directory.
    - The image cache directory.
    - The general config.
    - The denoiser config.
    - The save only mask flag.
    - The save only cleaned flag.
    - The extract text flag.
    - The separate noise mask flag.
    - The show masks flag. (when true, save intermediate masks to the cache directory)
    - The debug flag.
    """

    json_path: Path
    output_dir: Path
    cache_dir: Path
    general_config: cfg.GeneralConfig
    denoiser_config: cfg.DenoiserConfig
    save_only_mask: bool
    save_only_cleaned: bool
    extract_text: bool
    separate_noise_masks: bool
    show_masks: bool
    debug: bool


@dataclass
class DenoiseAnalytic:
    """
    Analytics data to visualize the denoising performance.
    - The standard deviations of the mask selection process. They are shown here,
      due to them being relevant to the min-threshold for choosing masks to denoise.
    """

    std_deviations: tuple[float, ...]
