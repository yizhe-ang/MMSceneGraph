# dataset settings
dataset_type = 'VisualGenomeDataset'
data_root = 'data/visualgenome/'
img_norm_cfg = dict(
    mean=[123.675, 116.28, 103.53], std=[58.395, 57.12, 57.375], to_rgb=True)
train_pipeline = [
    dict(type='LoadImageFromFile'),
    dict(type='LoadAnnotations', with_bbox=True, with_rel=True),
    dict(type='Resize', img_scale=(1333, 800), keep_ratio=True),
    dict(type='RandomFlip', flip_ratio=0.5),
    dict(type='Normalize', **img_norm_cfg),
    dict(type='Pad', size_divisor=32),
    dict(type='DefaultFormatBundle'),
    dict(type='Collect', keys=['img', 'gt_bboxes', 'gt_labels', 'gt_rels', 'gt_relmaps']),
]
test_pipeline = [
    dict(type='LoadImageFromFile'),
    # Since the forward process may need gt info, annos must be loaded.
    dict(type='LoadAnnotations', with_bbox=True, with_rel=True),
    dict(
        type='MultiScaleFlipAug',
        img_scale=(1333, 800),
        flip=False,
        transforms=[
            dict(type='Resize', keep_ratio=True),
            dict(type='RandomFlip'),
            dict(type='Normalize', **img_norm_cfg),
            dict(type='Pad', size_divisor=32),
            # NOTE: Do not change the img to DC.
            dict(type='ImageToTensor', keys=['img']),
            dict(type='ToTensor', keys=['gt_bboxes', 'gt_labels']),
            dict(type='ToDataContainer', fields=(dict(key='gt_bboxes'), dict(key='gt_labels'))),
            dict(type='Collect', keys=['img', 'gt_bboxes', 'gt_labels']),
        ])
]
data = dict(
    imgs_per_gpu=8,
    workers_per_gpu=2,
    train=dict(
        type=dataset_type,
        roidb_file=data_root + 'VG-SGG-with-attri.h5',
        dict_file=data_root + 'VG-SGG-dicts-with-attri.json',
        image_file=data_root + 'recsize_image_data.json',
        pipeline=train_pipeline,
        num_im=-1,
        num_val_im=5000,
        split='train',
        img_prefix=data_root + 'Images/'),
    val=dict(
        type=dataset_type,
        roidb_file=data_root + 'VG-SGG-with-attri.h5',
        dict_file=data_root + 'VG-SGG-dicts-with-attri.json',
        image_file=data_root + 'recsize_image_data.json',
        pipeline=test_pipeline,
        num_im=-1,
        num_val_im=5000,
        split='val',
        img_prefix=data_root + 'Images/'),
    test=dict(
        type=dataset_type,
        roidb_file=data_root + 'VG-SGG-with-attri.h5',
        dict_file=data_root + 'VG-SGG-dicts-with-attri.json',
        image_file=data_root + 'recsize_image_data.json',
        pipeline=test_pipeline,
        num_im=-1,
        split='test',
        img_prefix=data_root + 'Images/'))
# model settings
dataset_config = data['train'].copy()
dataset_config.update(dict(cache=data_root + 'VG_statistics.cache'))
model = dict(
    type='MaskRCNN',
    # FIXME: Many weights do not match
    # pretrained='checkpoints/mmlab/imnet/resnext101_64x4d-ee2c6f71.pth',
    # pretrained='checkpoints/mask_rcnn_x101_64x4d_fpn_2x_20181218-ea936e44.pth',
    backbone=dict(
        type='ResNeXt',
        depth=101,
        groups=64,
        base_width=4,
        num_stages=4,
        out_indices=(0, 1, 2, 3),
        frozen_stages=1,
        norm_cfg=dict(type='BN', requires_grad=True),
        style='pytorch'),
    neck=dict(
        type='FPN',
        in_channels=[256, 512, 1024, 2048],
        out_channels=256,
        num_outs=5),
    rpn_head=dict(
        type='RPNHead',
        in_channels=256,
        feat_channels=256,
        anchor_scales=[8],
        anchor_ratios=[0.5, 1.0, 2.0],
        anchor_strides=[4, 8, 16, 32, 64],
        target_means=[.0, .0, .0, .0],
        target_stds=[1.0, 1.0, 1.0, 1.0],
        loss_cls=dict(
            type='CrossEntropyLoss', use_sigmoid=True, loss_weight=1.0),
        loss_bbox=dict(type='SmoothL1Loss', beta=1.0 / 9.0, loss_weight=1.0)),
    bbox_roi_extractor=dict(
        type='SingleRoIExtractor',
        roi_layer=dict(type='RoIAlign', out_size=7, sample_num=2),
        out_channels=256,
        featmap_strides=[4, 8, 16, 32]),
    bbox_head=dict(
        type='ExtrDetWeightSharedFCBBoxHead',
        num_fcs=2,
        extract_type='concat',
        in_channels=256,
        fc_out_channels=1024,
        roi_feat_size=7,
        num_classes=151,
        target_means=[0., 0., 0., 0.],
        target_stds=[0.1, 0.1, 0.2, 0.2],
        reg_class_agnostic=False,
        loss_cls=dict(
            type='CrossEntropyLoss', use_sigmoid=False, loss_weight=1.0),
        loss_bbox=dict(type='SmoothL1Loss', beta=1.0, loss_weight=1.0)),
    mask_roi_extractor=dict(
        type='SingleRoIExtractor',
        roi_layer=dict(type='RoIAlign', out_size=14, sample_num=2),
        out_channels=256,
        featmap_strides=[4, 8, 16, 32]),
    mask_head=dict(
        type='TransferMaskHead',
        num_convs=4,
        in_channels=256,
        conv_out_channels=256,
        num_classes=151,
        transfer_cfg=dict(num_fc=2, fc_in=5120, hidden_neurons=[1024, 256], relu='LeakyReLU', mlp_fusion=True),
        loss_mask=dict(
            type='CrossEntropyLoss', use_mask=True, loss_weight=1.0)),
    relation_head=dict(
        type='MotifHead',
        dataset_config=dataset_config,
        num_classes=151,
        num_predicates=51,
        use_bias=True,
        head_config=dict(
            use_gt_box=False,
            use_gt_label=False,
            use_vision=True,
            embed_dim=200,
            hidden_dim=512,
            roi_dim=1024,
            context_pooling_dim=4096,
            dropout_rate=0.2,
            context_object_layer=1,
            context_edge_layer=1,
            glove_dir='data/glove/',
            causal_effect_analysis=False),
        bbox_roi_extractor=dict(
            type='VisualSpatialExtractor',
            bbox_roi_layer=dict(type='RoIAlign', out_size=7, sample_num=2),
            # mask_roi_layer=dict(type='ShapeAwareRoIAlign', out_size=7, sample_num=2),
            with_visual_bbox=True,
            with_visual_mask=False,
            with_visual_point=False,
            with_spatial=False,
            in_channels=256,
            fc_out_channels=1024,
            featmap_strides=[4, 8, 16, 32]),
        relation_roi_extractor=dict(
            type='VisualSpatialExtractor',
            bbox_roi_layer=dict(type='RoIAlign', out_size=7, sample_num=2),
            # mask_roi_layer=dict(type='ShapeAwareRoIAlign', out_size=7, sample_num=2),
            with_visual_bbox=True,
            with_visual_mask=False,
            with_visual_point=False,
            with_spatial=True,
            separate_spatial=False,
            in_channels=256,
            fc_out_channels=1024,
            featmap_strides=[4, 8, 16, 32]),
        relation_sampler=dict(
            type='Motif',
            pos_iou_thr=0.5,
            require_overlap=False,  # for sgdet training, not require
            num_sample_per_gt_rel=4,
            num_rel_per_image=1024,
            pos_fraction=0.25,
            test_overlap=True  # for testing
        ),
        loss_object=dict(
            type='CrossEntropyLoss', use_sigmoid=False, loss_weight=1.0),
        loss_relation=dict(
            type='CrossEntropyLoss', use_sigmoid=False, loss_weight=1.0)))
# model training and testing settings
train_cfg = dict(
    rpn=dict(
        assigner=dict(
            type='MaxIoUAssigner',
            pos_iou_thr=0.7,
            neg_iou_thr=0.3,
            min_pos_iou=0.3,
            ignore_iof_thr=-1),
        sampler=dict(
            type='RandomSampler',
            num=256,
            pos_fraction=0.5,
            neg_pos_ub=-1,
            add_gt_as_proposals=False),
        allowed_border=0,
        pos_weight=-1,
        debug=False),
    rpn_proposal=dict(
        nms_across_levels=False,
        nms_pre=2000,
        nms_post=2000,
        max_num=2000,
        nms_thr=0.7,
        min_bbox_size=0),
    rcnn=dict(
        assigner=dict(
            type='MaxIoUAssigner',
            pos_iou_thr=0.5,
            neg_iou_thr=0.5,
            min_pos_iou=0.5,
            ignore_iof_thr=-1),
        sampler=dict(
            type='RandomSampler',
            num=512,
            pos_fraction=0.25,
            neg_pos_ub=-1,
            add_gt_as_proposals=True),
        mask_size=28,
        pos_weight=-1,
        debug=False))
test_cfg = dict(
    rpn=dict(
        nms_across_levels=False,
        nms_pre=1000,
        nms_post=1000,
        max_num=1000,
        nms_thr=0.7,
        min_bbox_size=0),
    rcnn=dict(
        score_thr=0.05,  # When for demo, you'd better set it as 0.5 to filter the boxes.
        nms=dict(type='nms', iou_thr=0.5),
        max_per_img=50,  # Follow the setting in TDE, 80 Bboxes are selected; use 30 (mem limited)
        mask_thr_binary=0.5,
        rle_mask_encode=False,  # do not transform the mask into rle.
        crop_mask=True,  # so that the mask shape is the same as bbox, instead of image shape
        format_mask_result=False,  # do not transform to the result format like bbox
        to_tensor=True))
find_unused_parameters = True
evaluation = dict(interval=25, metric='sgdet', relation_mode=True, classwise=True)
# optimizer
optimizer = dict(type='SGD', lr=0.06, momentum=0.9, weight_decay=0.0001,
                 freeze_modules=['backbone', 'neck', 'rpn_head', 'bbox_head', 'mask_head'])
optimizer_config = dict(grad_clip=dict(max_norm=35, norm_type=2))
# learning policy
lr_config = dict(
    policy='step',
    warmup='linear',
    warmup_iters=500,
    warmup_ratio=1.0 / 3,
    step=[7, 10])
checkpoint_config = dict(interval=1)

# yapf:enable
# runtime settings
# FIXME:
total_epochs = 1
# total_epochs = 12
dist_params = dict(backend='nccl')
log_level = 'INFO'
work_dir = './new_experiments/VG_SgDet_motif_mask_X_rcnn_x101_64x4d_fpn_1x'
# FIXME: Weights not available
# load_from = './experiments/VG_COCOremap_MASKTRANS_mask_rcnn_x101_64x4d_fpn_1x/latest.pth'
# load_mapping = dict(align_dict={'relation_head.bbox_roi_extractor.visual_bbox_head': 'bbox_head.shared_fcs',
#                                 'relation_head.relation_roi_extractor.visual_bbox_head': 'bbox_head.shared_fcs'})
resume_from = None
workflow = [('train', 1)]

# yapf:disable
log_config = dict(
    interval=50,
    hooks=[
        dict(type='TextLoggerHook'),
        dict(type='TensorboardLoggerHook'),
        # FIXME:
        # dict(type='WandbLoggerHook',
        #      init_kwargs=dict(
        #          project=work_dir.split('/')[-1],
        #          name='train-1',
        #          config=work_dir + '/cfg.yaml'))
    ])
