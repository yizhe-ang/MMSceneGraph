import random
from collections import OrderedDict

import numpy as np
import torch
import torch.distributed as dist
from mmcv.parallel import MMDataParallel, MMDistributedDataParallel
from mmcv.runner import DistSamplerSeedHook, Runner

from mmdet.core import (DistEvalHook, DistOptimizerHook, Fp16OptimizerHook, EvalHook,
                        build_optimizer, CaptionEvalHook, CaptionDistEvalHook)
from mmdet.patches import PatchRunner, NoamLrUpdateHook, SamplingScheduleHook
from mmdet.datasets import build_dataloader, build_dataset
from mmdet.utils import get_root_logger


def set_random_seed(seed, deterministic=False):
    """Set random seed.

    Args:
        seed (int): Seed to be used.
        deterministic (bool): Whether to set the deterministic option for
            CUDNN backend, i.e., set `torch.backends.cudnn.deterministic`
            to True and `torch.backends.cudnn.benchmark` to False.
            Default: False.
    """
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    if deterministic:
        torch.backends.cudnn.deterministic = True
        torch.backends.cudnn.benchmark = False


def parse_losses(losses):
    log_vars = OrderedDict()
    for loss_name, loss_value in losses.items():
        if isinstance(loss_value, torch.Tensor):
            log_vars[loss_name] = loss_value.mean()
        elif isinstance(loss_value, list):
            log_vars[loss_name] = sum(_loss.mean() for _loss in loss_value)
        else:
            raise TypeError(
                '{} is not a tensor or list of tensors'.format(loss_name))

    loss = sum(_value for _key, _value in log_vars.items() if 'loss' in _key)

    log_vars['loss'] = loss
    for loss_name, loss_value in log_vars.items():
        # reduce loss when distributed training
        if dist.is_available() and dist.is_initialized():
            loss_value = loss_value.data.clone()
            dist.all_reduce(loss_value.div_(dist.get_world_size()))
        log_vars[loss_name] = loss_value.item()

    return loss, log_vars


def batch_processor(model, data, train_mode):
    """Process a data batch.

    This method is required as an argument of Runner, which defines how to
    process a data batch and obtain proper outputs. The first 3 arguments of
    batch_processor are fixed.

    Args:
        model (nn.Module): A PyTorch model.
        data (dict): The data batch in a dict.
        train_mode (bool): Training mode or not. It may be useless for some
            models.

    Returns:
        dict: A dict containing losses and log vars.
    """
    losses = model(**data)
    loss, log_vars = parse_losses(losses)
    if torch.isnan(loss):
        print([f['filename'] for f in data['img_meta'].data[0]])
        exit(0)
    outputs = dict(
        loss=loss, log_vars=log_vars, num_samples=len(data['img'].data))

    return outputs


def train_detector(model,
                   dataset,
                   cfg,
                   distributed=False,
                   validate=False,
                   timestamp=None,
                   meta=None):
    logger = get_root_logger(cfg.log_level)

    # prepare data loaders
    dataset = dataset if isinstance(dataset, (list, tuple)) else [dataset]

    data_loaders = [
        build_dataloader(
            ds,
            cfg.data.imgs_per_gpu,
            cfg.data.workers_per_gpu,
            # cfg.gpus will be ignored if distributed
            len(cfg.gpu_ids),
            dist=distributed,
            seed=cfg.seed) for ds in dataset
    ]

    # Note: To freeze some parameters, they should be frozen before wrapped into DDP.
    # build runner
    optimizer = build_optimizer(model, cfg.optimizer)

    # put model on gpus
    if distributed:
        find_unused_parameters = cfg.get('find_unused_parameters', False)
        # Sets the `find_unused_parameters` parameter in
        # torch.nn.parallel.DistributedDataParallel
        model = MMDistributedDataParallel(
            model.cuda(),
            device_ids=[torch.cuda.current_device()],
            broadcast_buffers=False,
            find_unused_parameters=find_unused_parameters)
    else:
        model = MMDataParallel(model.cuda(cfg.gpu_ids[0]), device_ids=cfg.gpu_ids)

    runner = PatchRunner(
        model,
        batch_processor,
        optimizer,
        cfg.work_dir,
        logger=logger,
        meta=meta)
    # an ugly walkaround to make the .log and .log.json filenames the same
    runner.timestamp = timestamp

    # fp16 setting
    fp16_cfg = cfg.get('fp16', None)
    if fp16_cfg is not None:
        optimizer_config = Fp16OptimizerHook(**cfg.optimizer_config,
                                             **fp16_cfg, distributed=distributed)
    elif distributed:
        optimizer_config = DistOptimizerHook(**cfg.optimizer_config)
    else:
        optimizer_config = cfg.optimizer_config

    # NoamLrUpdateHook
    if cfg.lr_config.policy == 'noam':
        cfg.lr_config.pop('policy')
        lr_config = NoamLrUpdateHook(optimizer=optimizer, **cfg.lr_config)
    else:
        lr_config = cfg.lr_config

    # register hooks
    runner.register_training_hooks(lr_config, optimizer_config,
                                   cfg.checkpoint_config, cfg.log_config,
                                   cfg.get('lr_first', True))
    if distributed:
        runner.register_hook(DistSamplerSeedHook())

    # register eval hooks
    if validate:
        val_dataset = build_dataset(cfg.data.val, dict(test_mode=True))
        val_dataloader = build_dataloader(
            val_dataset,
            imgs_per_gpu=1,
            workers_per_gpu=cfg.data.workers_per_gpu,
            dist=distributed,
            shuffle=False)
        # also eval on test split
        eval_cfg = cfg.get('evaluation', {})
        eval_hook = DistEvalHook if distributed else EvalHook
        runner.register_hook(eval_hook(val_dataloader, **eval_cfg))

    if hasattr(cfg, 'resume_from') and cfg.resume_from is not None:
        resume_config = cfg.resume_config if hasattr(cfg, 'resume_config') else None
        if resume_config is not None:
            runner.resume(cfg.resume_from, **resume_config)
        else:
            runner.resume(cfg.resume_from)
    elif hasattr(cfg, 'load_from') and cfg.load_from is not None:
        load_mapping = cfg.load_mapping if hasattr(cfg, 'load_mapping') else None
        runner.load_checkpoint(cfg.load_from, load_mapping)
        # OPTIONAL: load partitial module sequentially
        # E.g.: When perfrom SGG on a dataset without relationship annotation, you may need it.
        if hasattr(cfg, 'load_seqs'):
            for seq in cfg.load_seqs:
                runner.load_checkpoint(seq, load_mapping)

    runner.run(data_loaders, cfg.workflow, cfg.total_epochs)


def caption_batch_processor(model, data, train_mode):
    """Process a data batch.

    This method is required as an argument of Runner, which defines how to
    process a data batch and obtain proper outputs. The first 3 arguments of
    batch_processor are fixed.

    Args:
        model (nn.Module): A PyTorch model.
        data (dict): The data batch in a dict.
        train_mode (bool): Training mode or not. It may be useless for some
            models.

    Returns:
        dict: A dict containing losses and log vars.
    """
    losses = model(**data)
    loss, log_vars = parse_losses(losses)

    outputs = dict(
        loss=loss, log_vars=log_vars, num_samples=len(data['img_meta'].data))

    return outputs

def train_captioner(model,
                    dataset,
                    cfg,
                    distributed=False,
                    validate=False,
                    validate_test=False,
                    timestamp=None,
                    meta=None):
    logger = get_root_logger(cfg.log_level)

    # prepare data loaders
    dataset = dataset if isinstance(dataset, (list, tuple)) else [dataset]
    data_loaders = [
        build_dataloader(
            ds,
            cfg.data.imgs_per_gpu,
            cfg.data.workers_per_gpu,
            # cfg.gpus will be ignored if distributed
            len(cfg.gpu_ids),
            dist=distributed,
            seed=cfg.seed) for ds in dataset
    ]

    # Note: To freeze some parameters, they should be frozen before wrapped into DDP.
    # build runner
    optimizer = build_optimizer(model, cfg.optimizer)

    # put model on gpus
    if distributed:
        find_unused_parameters = cfg.get('find_unused_parameters', False)
        # Sets the `find_unused_parameters` parameter in
        # torch.nn.parallel.DistributedDataParallel
        model = MMDistributedDataParallel(
            model.cuda(),
            device_ids=[torch.cuda.current_device()],
            broadcast_buffers=False,
            find_unused_parameters=find_unused_parameters)
    else:
        model = MMDataParallel(model.cuda(cfg.gpu_ids[0]), device_ids=cfg.gpu_ids)

    runner = PatchRunner(
        model,
        caption_batch_processor,
        optimizer,
        cfg.work_dir,
        logger=logger,
        meta=meta)
    # an ugly walkaround to make the .log and .log.json filenames the same
    runner.timestamp = timestamp

    # fp16 setting
    fp16_cfg = cfg.get('fp16', None)
    if fp16_cfg is not None:
        optimizer_config = Fp16OptimizerHook(**cfg.optimizer_config,
                                             **fp16_cfg, distributed=distributed)
    elif distributed:
        optimizer_config = DistOptimizerHook(**cfg.optimizer_config)
    else:
        optimizer_config = cfg.optimizer_config

    # NoamLrUpdateHook
    if cfg.lr_config.policy == 'noam':
        cfg.lr_config.pop('policy')
        lr_config = NoamLrUpdateHook(optimizer=optimizer, **cfg.lr_config)
    else:
        lr_config = cfg.lr_config

    # register hooks:
    # Note: here the optimizer is firstly called, then lr scheduler
    runner.register_training_hooks(lr_config, optimizer_config,
                                   cfg.checkpoint_config, cfg.log_config, lr_first=False)
    if distributed:
        runner.register_hook(DistSamplerSeedHook())

    # register sampling schedule hook
    runner.register_hook(SamplingScheduleHook(**(cfg.sampling_schedule_config)))

    # register eval hooks
    if validate:
        val_dataset = build_dataset(cfg.data.val, dict(test_mode=True))
        val_dataloader = build_dataloader(
            val_dataset,
            imgs_per_gpu=1,
            workers_per_gpu=cfg.data.workers_per_gpu,
            dist=distributed,
            shuffle=False)
        eval_cfg = cfg.get('evaluation', {})
        eval_hook = CaptionDistEvalHook if distributed else CaptionEvalHook
        runner.register_hook(eval_hook(val_dataloader, **eval_cfg))

    # also eval on test split
    if validate_test:
        test_dataset = build_dataset(cfg.data.test, dict(test_mode=True))
        test_dataloader = build_dataloader(
            test_dataset,
            imgs_per_gpu=1,
            workers_per_gpu=cfg.data.workers_per_gpu,
            dist=distributed,
            shuffle=False)
        eval_cfg = cfg.get('evaluation', {})
        eval_hook = CaptionDistEvalHook if distributed else CaptionEvalHook
        runner.register_hook(eval_hook(test_dataloader, **eval_cfg))

    if cfg.resume_from:
        resume_config = cfg.resume_config if hasattr(cfg, 'resume_config') else None
        if resume_config is not None:
            runner.resume(cfg.resume_from, **resume_config)
        else:
            runner.resume(cfg.resume_from)
    elif cfg.load_from:
        load_mapping = cfg.load_mapping if hasattr(cfg, 'load_mapping') else None
        runner.load_checkpoint(cfg.load_from, load_mapping)
        # OPTIONAL: load partitial module sequentially
        # E.g.: When perfrom SGG on a dataset without relationship annotation, you may need it.
        if hasattr(cfg, 'load_seqs'):
            for seq in cfg.load_seqs:
                runner.load_checkpoint(seq, load_mapping)

    runner.run(data_loaders, cfg.workflow, cfg.total_epochs)
