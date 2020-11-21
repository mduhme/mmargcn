import torch
import torch.nn as nn

import models.mmargcn.agcn as agcn
from models.mmargcn.fusion import get_fusion, get_skeleton_imu_fusion_graph
from models.mmargcn.rgb_feature_models import RgbCnnEncoder, RgbR2P1DEncoder


class SkeletonImuEnhancedModel(nn.Module):
    """
    Take skeleton data with additional joints for each imu modality and append them to different parts of the skeleton.
    """

    def __init__(self, data_shape, num_classes: int, graph, **kwargs):
        super().__init__()
        num_layers = kwargs.get("num_layers", 10)
        skeleton_imu_graph = get_skeleton_imu_fusion_graph(graph, **kwargs)
        self.agcn = agcn.Model(data_shape["skeleton"], num_classes, skeleton_imu_graph, num_layers=num_layers,
                               without_fc=kwargs.get("without_fc", False))

    def forward(self, x):
        return self.agcn(x)


class SkeletonRgbEarlyFusion(nn.Module):
    def __init__(self, data_shape, num_classes: int, graph, **kwargs):
        super().__init__()
        num_layers = kwargs.get("num_layers", 10)
        fusion_type = kwargs.get("fusion", "concatenate")

        self.rgb_encoder = RgbCnnEncoder(rgb_num_vertices=graph.num_vertices, **kwargs)

        if fusion_type == "concatenate":
            num_channels = data_shape["skeleton"][-1] + self.rgb_encoder.num_encoded_channels
        else:
            num_channels = data_shape["skeleton"][-1]

        agcn_input_shape = (self.rgb_encoder.num_bodies, data_shape["rgb"][0], graph.num_vertices,
                            num_channels)
        self.agcn = agcn.Model(agcn_input_shape, num_classes, graph, num_layers=num_layers,
                               without_fc=kwargs.get("without_fc", False))
        self.fusion = get_fusion(fusion_type, concatenate_dim=-1)

    def forward(self, x):
        skeleton_data = x["skeleton"]
        rgb_data = x["rgb"]

        # Encode RGB images
        rgb_data = self.rgb_encoder(rgb_data)

        # Early fusion of skeleton and rgb
        fused_data = self.fusion.combine(skeleton_data, rgb_data)

        # Run graph convolutional neural network
        y = self.agcn(fused_data)
        return y


class SkeletonImuRgbEarlyFusion(nn.Module):
    def __init__(self, data_shape, num_classes: int, graph, **kwargs):
        super().__init__()
        num_layers = kwargs.get("num_layers", 10)
        skeleton_imu_graph = get_skeleton_imu_fusion_graph(graph, **kwargs)
        fusion_type = kwargs.get("fusion", "concatenate")

        self.rgb_encoder = RgbCnnEncoder(rgb_num_vertices=skeleton_imu_graph.num_vertices,
                                         rgb_num_bodies=data_shape["skeleton"][0], **kwargs)

        if fusion_type == "concatenate":
            num_channels = data_shape["skeleton"][-1] + self.rgb_encoder.num_encoded_channels
        else:
            num_channels = data_shape["skeleton"][-1]

        agcn_input_shape = (self.rgb_encoder.num_bodies, data_shape["rgb"][0], skeleton_imu_graph.num_vertices,
                            num_channels)
        self.agcn = agcn.Model(agcn_input_shape, num_classes, skeleton_imu_graph, num_layers=num_layers,
                               without_fc=kwargs.get("without_fc", False))
        self.fusion = get_fusion(fusion_type, concatenate_dim=-1)

    def forward(self, x):
        skeleton_data = x["skeleton"]
        rgb_data = x["rgb"]

        # Encode RGB images
        rgb_data = self.rgb_encoder(rgb_data)

        # Early fusion of skeleton and rgb
        fused_data = self.fusion.combine(skeleton_data, rgb_data)

        # Run graph convolutional neural network
        y = self.agcn(fused_data)
        return y


class SkeletonRgbR2P1DEarlyFusion(nn.Module):
    def __init__(self, data_shape, num_classes: int, graph, **kwargs):
        super().__init__()
        num_layers = kwargs.get("num_layers", 10)

        num_additional_nodes = kwargs.pop("num_additional_nodes", 3)
        self.rgb_encoder = RgbR2P1DEncoder(num_encoded_channels=data_shape["skeleton"][-1],
                                           num_additional_nodes=num_additional_nodes * data_shape["skeleton"][0],
                                           **kwargs)

        graph = graph.with_new_edges([(graph.num_vertices + i, graph.center_joint)
                                      for i in range(num_additional_nodes)])

        agcn_input_shape = list(data_shape["skeleton"])
        agcn_input_shape[2] = graph.num_vertices
        self.agcn = agcn.Model(tuple(agcn_input_shape), num_classes, graph, num_layers=num_layers,
                               without_fc=kwargs.get("without_fc", False))

    def forward(self, x):
        skeleton_data = x["skeleton"]
        rgb_data = x["rgb"]

        # Encode RGB images
        rgb_data = self.rgb_encoder(rgb_data)
        rgb_data = rgb_data.view(*rgb_data.shape[:3], skeleton_data.shape[1], -1)
        rgb_data = rgb_data.permute(0, 3, 2, 4, 1)

        # Append additional RGB nodes to graph
        fused_data = torch.cat((skeleton_data, rgb_data), dim=3)

        # Run graph convolutional neural network
        y = self.agcn(fused_data)
        return y