"""
This code uses OpenPose compiled from source (https://github.com/CMU-Perceptual-Computing-Lab/openpose).
Use Python 3.7 when configuring/compiling and executing this code.
As of creation of this file (07.10.2020), Python 3.8+ is not working with compiled OpenPose.
"""

import abc
import argparse
import os

import cv2
import numpy as np
from tqdm import tqdm
from pathlib import Path
from typing import List, Optional

from tools.openpose.openpose_wrapper import OpenPose, body_score


class Dataset:
    def __init__(self, in_path: str, out_path: str, max_bodies: int):
        self.in_path = in_path
        self.out_path = out_path
        self.max_bodies = max_bodies

    @abc.abstractmethod
    def get_input_files(self) -> List[str]:
        pass

    def _create_output_path(self, input_path: str):
        p = os.path.splitext(input_path)[0]
        p = p[len(self.in_path) + 1:] + ".npy"
        p = os.path.abspath(os.path.join(self.out_path, p))
        return p

    def get_output_files(self, input_files: List[str]) -> List[str]:
        return [self._create_output_path(p) for p in input_files]


class MHADDataset(Dataset):
    def __init__(self, in_path: Optional[str], out_path: Optional[str], max_bodies: int = 1):
        default_in_path = "../unprocessed_data/UTD-MHAD/RGB"
        default_out_path = "../unprocessed_data/UTD-MHAD/OpenPose"
        super().__init__(in_path or default_in_path, out_path or default_out_path, max_bodies)

    def get_input_files(self) -> List[str]:
        return list(map(str, Path(self.in_path).rglob("*.[aA][vV][iI]")))


class MMActDataset(Dataset):
    def __init__(self, in_path: Optional[str], out_path: Optional[str], max_bodies: int = 2):
        default_in_path = "../unprocessed_data/MMAct/RGB"
        default_out_path = "../unprocessed_data/MMAct/OpenPose"
        super().__init__(in_path or default_in_path, out_path or default_out_path, max_bodies)

    def get_input_files(self) -> List[str]:
        return list(map(str, Path(self.in_path).rglob("*.[mM][pP]4")))


DATASETS = {
    "utd_mhad": MHADDataset,
    "mmact": MMActDataset
}


def get_config() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="OpenPose skeleton generation.")
    parser.add_argument("-d", "--dataset", default="utd_mhad", type=str, choices=list(DATASETS.keys()),
                        help="Which dataset to process.")
    parser.add_argument("--in_path", type=str,
                        help="Unprocessed data directory. If not specified, use default path for dataset.")
    parser.add_argument("--out_path", type=str, help="Destination directory for processed data. "
                                                     "If not specified, use default path for dataset.")
    parser.add_argument("--openpose_binary_path", default="../../Repos/openpose/build_windows/x64/Release", type=str,
                        help="Path to openpose binaries")
    parser.add_argument("--openpose_python_path", default="../../Repos/openpose/build_windows/python/openpose/Release",
                        type=str, help="Path to openpose python binaries")
    parser.add_argument("--output_images", action="store_true",
                        help="If specified, output images with visible skeleton.")
    parser.add_argument("--model_pose", default="BODY_25", type=str, help="Which pose model to use.")
    parser.add_argument("--skip_existing", action="store_true", help="Skip file if output already exists.")
    parser.add_argument("--debug", action="store_true", help="Only convert first found file.")
    args = parser.parse_args()

    if args.dataset not in DATASETS:
        raise ValueError(f"Dataset '{args.dataset}' not available.")

    return args


def convert_video_file(input_path: str, output_path: str, max_allowed_bodies: int, openpose: OpenPose,
                       output_images: bool = False):
    # Ensure output path exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # Detect skeletons in all video frames
    v = cv2.VideoCapture(input_path)
    pose_predicted_frames = openpose.estimate_pose_video(v)
    v.release()

    def has_elements(array: np.ndarray):
        return bool(array.ndim) and bool(array.size)

    # shape of skeletons is (num_frames, num_joints, 3 [= x-coord, y-coord, probability], num_bodies)
    max_bodies = np.max([pose_frame.poseKeypoints.shape[0]
                         for pose_frame in pose_predicted_frames if has_elements(pose_frame.poseKeypoints)])
    num_joints = next(pose_frame.poseKeypoints.shape[1]
                      for pose_frame in pose_predicted_frames if has_elements(pose_frame.poseKeypoints))

    # Filter skeletons (sometimes random objects are detected as skeletons) and merge them in a single array.
    skeletons = np.zeros((len(pose_predicted_frames), num_joints, 3, max_allowed_bodies))
    for frame_idx, pose_frame in enumerate(pose_predicted_frames):
        if not has_elements(pose_frame.poseKeypoints):
            continue

        num_bodies = pose_frame.poseKeypoints.shape[0]
        if num_bodies > max_allowed_bodies:
            scores = np.array([body_score(body) for body in pose_frame.poseKeypoints])
            score_mask = scores.argsort()[::-1][:max_allowed_bodies]
            skeleton = np.moveaxis(pose_frame.poseKeypoints[score_mask], 0, -1).astype(skeletons.dtype)
        else:
            skeleton = np.moveaxis(pose_frame.poseKeypoints, 0, -1).astype(skeletons.dtype)
        skeletons[frame_idx] = skeleton

    # Save numpy array
    np.save(output_path, skeletons)

    if output_images:
        image_out_path = os.path.splitext(output_path)[0]
        os.makedirs(image_out_path, exist_ok=True)
        for idx, pose_frame in enumerate(pose_predicted_frames):
            if not output_images and max_bodies > 1 and pose_frame.poseKeypoints.shape[0] < 2:
                continue
            cv2.imwrite(os.path.join(image_out_path, f"{idx:03d}.png"), pose_frame.cvOutputData)


def run_conversion(args: argparse.Namespace, dataset: Dataset):
    dataset.out_path = os.path.join(dataset.out_path, cf.model_pose)
    input_files = dataset.get_input_files()
    output_files = dataset.get_output_files(input_files)
    if args.debug:
        input_files = input_files[:1]
        output_files = output_files[:1]

    with OpenPose(args.openpose_binary_path, args.openpose_python_path, args.model_pose) as openpose:
        for input_file, output_file in tqdm(zip(input_files, output_files),
                                            "Detecting Openpose skeletons in video files", total=len(input_files)):
            if args.skip_existing and os.path.exists(output_file):
                continue
            convert_video_file(input_file, output_file, dataset.max_bodies, openpose, args.output_images)


if __name__ == "__main__":
    cf = get_config()
    run_conversion(cf, DATASETS[cf.dataset](cf.in_path, cf.out_path))
