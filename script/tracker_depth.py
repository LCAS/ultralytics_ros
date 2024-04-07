#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ultralytics_ros
# Copyright (C) 2023-2024  Alpaca-zip
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import cv_bridge
import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from rclpy.node import Node
from sensor_msgs.msg import Image
from PIL import ImageDraw, ImageFont
from PIL import Image as im
from ultralytics import YOLO
from std_msgs.msg import Float64
from vision_msgs.msg import Detection2D, Detection2DArray, ObjectHypothesisWithPose
from ultralytics_ros.msg import YoloResult, YoloResultDistance, DistanceArray
from message_filters import ApproximateTimeSynchronizer, Subscriber


class TrackerNode(Node):
    def __init__(self):
        super().__init__("tracker_node")
        self.declare_parameter("yolo_model", "yolov8n.pt")
        self.declare_parameter("input_topic",  rclpy.Parameter.Type.STRING)
        self.declare_parameter("input_depth_topic", rclpy.Parameter.Type.STRING)
        self.declare_parameter("result_topic", rclpy.Parameter.Type.STRING)
        self.declare_parameter("result_image_topic", rclpy.Parameter.Type.STRING)
        self.declare_parameter("conf_thres", 0.25)
        self.declare_parameter("iou_thres", 0.45)
        self.declare_parameter("max_det", 300)
        self.declare_parameter("classes", list(range(80)))
        self.declare_parameter("tracker", "bytetrack.yaml")
        self.declare_parameter("device", "cpu")
        self.declare_parameter("result_conf", True)
        self.declare_parameter("result_line_width", 1)
        self.declare_parameter("result_font_size", 1)
        self.declare_parameter("result_font", "Arial.ttf")
        self.declare_parameter("result_labels", True)
        self.declare_parameter("result_boxes", True)

        path = get_package_share_directory("ultralytics_ros")
        yolo_model = self.get_parameter("yolo_model").get_parameter_value().string_value
        self.model = YOLO(f"{path}/models/{yolo_model}")
        self.model.fuse()

        self.bridge = cv_bridge.CvBridge()
        self.use_segmentation = yolo_model.endswith("-seg.pt")

        input_topic = (
            self.get_parameter("input_topic").get_parameter_value().string_value
        )
        input_depth_topic = (
            self.get_parameter("input_depth_topic").get_parameter_value().string_value
        )
        result_topic = (
            self.get_parameter("result_topic").get_parameter_value().string_value
        )
        result_image_topic = (
            self.get_parameter("result_image_topic").get_parameter_value().string_value
        )
        qos_policy = rclpy.qos.QoSProfile(reliability=rclpy.qos.ReliabilityPolicy.BEST_EFFORT,
                                          history=rclpy.qos.HistoryPolicy.KEEP_LAST,
                                          depth=1)
        # self.sub = self.create_subscription(Image, topic,
                                    # self.subscriber_callback, qos_profile=qos_policy)
        self.image_sub = Subscriber(self, Image, input_topic, qos_profile=qos_policy)
        self.depth_sub = Subscriber(self, Image, input_depth_topic, qos_profile=qos_policy)
        
        self.ts = ApproximateTimeSynchronizer([self.image_sub, self.depth_sub], queue_size=10, slop=0.1)
        self.ts.registerCallback(self.image_callback)

        self.results_pub = self.create_publisher(YoloResultDistance, result_topic, 1)
        self.result_image_pub = self.create_publisher(Image, result_image_topic, 1)

    def image_callback(self, msg, msg_depth):
        print("------------------IMAGE RECEIVED----------------")
        cv_image = self.bridge.imgmsg_to_cv2(msg, desired_encoding="bgr8")
        depth_image = self.bridge.imgmsg_to_cv2(msg_depth, desired_encoding='passthrough')
        conf_thres = self.get_parameter("conf_thres").get_parameter_value().double_value
        iou_thres = self.get_parameter("iou_thres").get_parameter_value().double_value
        max_det = self.get_parameter("max_det").get_parameter_value().integer_value
        # classes = (
        #     self.get_parameter("classes").get_parameter_value().integer_array_value
        # )
        classes = 0 #For person detection only
        tracker = self.get_parameter("tracker").get_parameter_value().string_value
        device = self.get_parameter("device").get_parameter_value().string_value or None
        results = self.model.track(
            source=cv_image,
            conf=conf_thres,
            iou=iou_thres,
            max_det=max_det,
            classes=classes,
            tracker=tracker,
            device=device,
            verbose=False,
            retina_masks=True,
        )

        if results is not None:
            yolo_result_msg = YoloResultDistance()
            yolo_result_image_msg = Image()
            yolo_result_msg.header = msg.header
            yolo_result_image_msg.header = msg.header
            # yolo_result_image_msg = self.create_result_image(results)
            # if self.use_segmentation:
            #     yolo_result_msg.masks = self.create_segmentation_masks(results)
            # yolo_result_msg.detections, yolo_result_msg.distances = self.create_detections_array(results,depth_image)
            yolo_result_image_msg, yolo_result_msg.detections, yolo_result_msg.distances = self.create_result(results,depth_image)
            if self.use_segmentation:
                yolo_result_msg.masks = self.create_segmentation_masks(results)
            self.results_pub.publish(yolo_result_msg)
            self.result_image_pub.publish(yolo_result_image_msg)

    def create_detections_array(self, results, depth_image):
        detections_msg = Detection2DArray()
        distances_msg = DistanceArray()
        distance_store = []
        bounding_box = results[0].boxes.xywh
        classes = results[0].boxes.cls
        confidence_score = results[0].boxes.conf
        for bbox, cls, conf in zip(bounding_box, classes, confidence_score):
            objectname = results[0].names.get(int(cls))
            if objectname == 'person': # where int(cls) == 0
                detection = Detection2D()
                detection.bbox.center.position.x = float(bbox[0])
                detection.bbox.center.position.y = float(bbox[1])
                detection.bbox.size_x = float(bbox[2])
                detection.bbox.size_y = float(bbox[3])
                hypothesis = ObjectHypothesisWithPose()
                hypothesis.hypothesis.class_id = objectname
                hypothesis.hypothesis.score = float(conf)
                distance_value = self.create_distance(bbox, depth_image)
                distance_store.append(np.float32(distance_value).item())
                distances_msg.distances.append(distance_value)
                detection.results.append(hypothesis)
                detections_msg.detections.append(detection)
        return detections_msg, distances_msg

    # funtion used to get depth imformation
    def create_distance(self, bbox, depth_image):
        x = round(float(bbox[0]))
        y = round(float(bbox[1]))
        width = round(float(bbox[2]))
        height = round(float(bbox[3]))
        lower_bound_x = max(0, x-round(width/6))
        upper_bound_x = min(depth_image.shape[1]-1, x+round(width/6))
        lower_bound_y = max(0, y-round(height/6))
        upper_bound_y = min(depth_image.shape[0]-1, y+round(height/6))
        counter = 0
        sum_dis = 0
        for a in (lower_bound_y, upper_bound_y):
            for b in (lower_bound_x, upper_bound_x):
                if not np.isnan(depth_image[a][b]):
                    counter = counter + 1
                    sum_dis = sum_dis + depth_image[a][b]
        if counter == 0:
            ave_dis = float('nan') 
        else:
            ave_dis = sum_dis/counter
        return ave_dis

    def create_result_image(self, results):
        result_conf = self.get_parameter("result_conf").get_parameter_value().bool_value
        result_line_width = (
            self.get_parameter("result_line_width").get_parameter_value().integer_value
        )
        result_font_size = (
            self.get_parameter("result_font_size").get_parameter_value().integer_value
        )
        result_font = (
            self.get_parameter("result_font").get_parameter_value().string_value
        )
        result_labels = (
            self.get_parameter("result_labels").get_parameter_value().bool_value
        )
        result_boxes = (
            self.get_parameter("result_boxes").get_parameter_value().bool_value
        )
        plotted_image = results[0].plot(
            conf=result_conf,
            line_width=result_line_width,
            font_size=result_font_size,
            font=result_font,
            labels=result_labels,
            boxes=result_boxes,
        )
        result_image_msg = self.bridge.cv2_to_imgmsg(plotted_image, encoding="bgr8")
        
        return result_image_msg
    
    # A combination of create_result_image() and create_detection array(), using information from both so one function makes it easier.
    def create_result(self, results, depth_image):
        result_conf = self.get_parameter("result_conf").get_parameter_value().bool_value
        result_line_width = (
            self.get_parameter("result_line_width").get_parameter_value().integer_value
        )
        result_font_size = (
            self.get_parameter("result_font_size").get_parameter_value().integer_value
        )
        result_font = (
            self.get_parameter("result_font").get_parameter_value().string_value
        )
        result_labels = (
            self.get_parameter("result_labels").get_parameter_value().bool_value
        )
        result_boxes = (
            self.get_parameter("result_boxes").get_parameter_value().bool_value
        )
        plotted_image = results[0].plot(
            conf=result_conf,
            line_width=result_line_width,
            font_size=result_font_size,
            font=result_font,
            labels=result_labels,
            boxes=result_boxes,
        )
        detections_msg = Detection2DArray()
        distances_msg = DistanceArray()
        distance_store = []
        bounding_box = results[0].boxes.xywh
        classes = results[0].boxes.cls
        confidence_score = results[0].boxes.conf
        plotted_image_distance = im.fromarray(plotted_image)
        for bbox, cls, conf in zip(bounding_box, classes, confidence_score):
            objectname = results[0].names.get(int(cls))
            if objectname == 'person': # where int(cls) == 0
                detection = Detection2D()
                detection.bbox.center.position.x = float(bbox[0])
                detection.bbox.center.position.y = float(bbox[1])
                detection.bbox.size_x = float(bbox[2])
                detection.bbox.size_y = float(bbox[3])
                hypothesis = ObjectHypothesisWithPose()
                hypothesis.hypothesis.class_id = objectname
                hypothesis.hypothesis.score = float(conf)
                distance_value = self.create_distance(bbox, depth_image)
                distance_store.append(np.float32(distance_value).item())
                distances_msg.distances.append(distance_value)
                detection.results.append(hypothesis)    
                ImageDraw.Draw(plotted_image_distance).text((detection.bbox.center.position.x, detection.bbox.center.position.y), str(np.float32(distance_value).item()), font = ImageFont.load_default(),fill =(255, 0, 0))
        plotted_image_distance =np.array(plotted_image_distance)
        result_image_msg = self.bridge.cv2_to_imgmsg(plotted_image_distance, encoding="bgr8")
        
        return result_image_msg, detections_msg, distances_msg


    def create_segmentation_masks(self, results):
        masks_msg = []
        for result in results:
            cls_list = np.array(result.boxes.cls.tolist())
            humanid_ii = np.where(cls_list == 0.0)[0]
            if len(humanid_ii) > 0:
                for i in humanid_ii:
                    if hasattr(result, "masks") and (result.masks[i] is not None):
                        mask_tensor = result.masks[i]
                        mask_numpy = (
                            np.squeeze(mask_tensor.data.to("cpu").detach().numpy()).astype(
                                np.uint8
                            )
                            * 255
                        )
                        mask_image_msg = self.bridge.cv2_to_imgmsg(
                            mask_numpy, encoding="mono8"
                        )
                        masks_msg.append(mask_image_msg)
        return masks_msg


def main(args=None):
    rclpy.init(args=args)
    node = TrackerNode()
    rclpy.spin(node)


if __name__ == "__main__":
    main()
