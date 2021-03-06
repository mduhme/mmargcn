import abc
from typing import Any, Iterable, List, Optional, Sequence

import cv2
import numpy as np
from scipy.io import loadmat

import util.preprocessing.video as video_util


class SequenceStructure:
    def __init__(self, max_sequence_length: int, input_shape: Sequence[int], target_type: type):
        self.max_sequence_length = max_sequence_length
        self.input_shape = input_shape
        self.target_type = target_type


class Loader:
    def __init__(self, name: str, frame_idx: int, structure: SequenceStructure):
        self.name = name.lower()
        self.frame_idx = frame_idx
        self.structure = structure

    @abc.abstractmethod
    def load_samples(self, files: Iterable[str]) -> Iterable[Any]:
        pass

    @abc.abstractmethod
    def load_samples_merged(self, files: Iterable[str]) -> np.ndarray:
        pass

    @abc.abstractmethod
    def compute_sequence_length(self, sample) -> int:
        pass

    @abc.abstractmethod
    def compute_sequence_lengths(self, raw_samples: Iterable[Any]) -> np.ndarray:
        """
        Compute length for each sequence in the given list of files.

        :param raw_samples: list of samples from a loader
        :return: 1D numpy array of size = len(files) that stores the length of each sequence
        """
        pass


class NumpyLoader(Loader):
    def __init__(self, name: str, structure: SequenceStructure, mmap_mode: Optional[str] = None,
                 permutation: Optional[Sequence[int]] = None):
        super().__init__(name, 0, structure)
        self._mmap_mode = mmap_mode
        self._permutation = None if permutation is None else np.array(permutation, dtype=np.int)

    def load_samples(self, files: Iterable[str]) -> Iterable[Any]:
        for file in files:
            a = np.load(file, self._mmap_mode)

            if self._permutation is not None:
                assert len(self._permutation) == a.ndim
                a = a.transpose(self._permutation)

            yield a.astype(self.structure.target_type)

    def load_samples_merged(self, files: Iterable[str]) -> np.ndarray:
        files = list(files)
        shape = (len(files), *self.structure.input_shape)
        data = np.zeros(shape, dtype=self.structure.target_type)
        for idx, d in enumerate(self.load_samples(files)):
            data[idx, :len(d)] = d.astype(self.structure.target_type)
        return data

    def compute_sequence_length(self, sample: np.ndarray) -> int:
        return len(sample)

    def compute_sequence_lengths(self, raw_samples: Iterable[Any]) -> np.ndarray:
        return np.array([self.compute_sequence_length(s) for s in raw_samples], dtype=np.int)


class MatlabLoader(Loader):
    def __init__(self, name: str, mat_id: str, frame_idx: int, structure: SequenceStructure,
                 permutation: Sequence[int]):
        super().__init__(name, frame_idx, structure)
        self._mat_id = mat_id
        self._permutation = permutation

    def load_samples(self, files: Iterable[str]) -> Iterable[np.ndarray]:
        for file in files:
            yield MatlabLoader.load_mat_to_numpy(file, self._mat_id, self.frame_idx, self.structure.max_sequence_length,
                                                 self.structure.target_type, self._permutation)

    def load_samples_merged(self, files: Iterable[str]) -> np.ndarray:
        return MatlabLoader.load_all_mat_to_numpy(list(files), self._mat_id, self.frame_idx,
                                                  self.structure.max_sequence_length, self.structure.input_shape,
                                                  self.structure.target_type, self._permutation)

    def compute_sequence_length(self, sample: np.ndarray) -> int:
        return len(sample)

    def compute_sequence_lengths(self, raw_samples: Iterable[np.ndarray]) -> np.ndarray:
        return np.array([self.compute_sequence_length(s) for s in raw_samples], dtype=np.int)

    @staticmethod
    def load_mat_to_numpy(file_name: str, mat_id: str, frame_dim: int, target_max_sequence_length: int,
                          target_type: type, permutation: Sequence[int]) -> np.ndarray:
        mat = loadmat(file_name)[mat_id]
        assert mat.shape[frame_dim] <= target_max_sequence_length
        return mat.transpose(permutation).astype(target_type)

    @staticmethod
    def load_all_mat_to_numpy(files: List[str], mat_id: str, frame_dim: int, target_max_sequence_length: int,
                              target_shape: Sequence[int], target_type: type, permutation: Sequence[int]) -> np.ndarray:
        def max_shape_dim(lst: list, dim: int):
            return np.max([s.shape[dim] for s in lst])

        data_list = [loadmat(f)[mat_id] for f in files]

        assert max_shape_dim(data_list, frame_dim) <= target_max_sequence_length

        shape = (len(files), *target_shape)
        data = np.zeros(shape, dtype=target_type)
        for idx, d in enumerate(data_list):
            d = d.transpose(permutation).astype(target_type)
            data[idx, :len(d)] = d
        return data


class RGBVideoLoader(Loader):
    def __init__(self, name: str, structure: SequenceStructure):
        super().__init__(name, -1, structure)

    def load_samples(self, files: Iterable[str]) -> Iterable[cv2.VideoCapture]:
        for file in files:
            video = video_util.load_video(file)
            yield video
            video.release()

    def load_samples_merged(self, files: Iterable[str]):
        raise RuntimeError("RGBVideoLoader: Merged allocation would require too much memory."
                           "Load and process each video individually instead.")

    def compute_sequence_length(self, video: cv2.VideoCapture) -> int:
        return int(video.get(cv2.CAP_PROP_FRAME_COUNT))

    def compute_sequence_lengths(self, videos: Iterable[cv2.VideoCapture]) -> np.ndarray:
        num_frames = []
        for video in videos:
            num_frames.append(self.compute_sequence_length(video))
        return np.array(num_frames, dtype=np.int)


class OpenposeBody25ToKinect1Loader(NumpyLoader):
    """
    Map body_25 skeleton to kinect1 skeleton structure.
    """

    def __init__(self, name: str, structure: SequenceStructure, permutation: Optional[Sequence[int]] = None,
                 spine_interpolation_factor=0.3):
        super().__init__(name, structure, permutation=permutation)
        self._mapping = [
            0, 1, 8, 8, 5, 6, 7, 7, 2, 3, 4, 4, 12, 13, 14, 19, 9, 10, 11, 22
        ]
        self._spine_interpolation_factor = spine_interpolation_factor

    def load_samples(self, files: Iterable[str]) -> Iterable[Any]:
        for sample in super().load_samples(files):
            # Map to Kinect1 and only get x and y, not probability
            sample_0 = sample[:, self._mapping, :2]
            # Interpolate 2 (spine) between 1 (shoulder center) and 3 (hip center)
            spine_coords = sample_0[:, 3] + (sample_0[:, 1] - sample_0[:, 3]) * self._spine_interpolation_factor
            sample_0[:, 2] = spine_coords
            # Coordinates are still float values -> round and cast to int
            sample_0 = np.rint(sample_0).astype(np.int)
            yield sample_0
