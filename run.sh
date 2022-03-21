# srun -p dsta --mpi=pmi2 --gres=gpu:1 -n1 --ntasks-per-node=1 --job-name=job --kill-on-bad-exit=1 -w SG-IDC1-10-51-2-73 \
# python -m pdb -c continue tools/train.py \
#     configs/scene_graph/VG_SgDet_motif_mask_X_rcnn_x101_64x4d_fpn_1x.py \
#     --validate

# configs/scene_graph/VG_PredCls_motif_mask_X_rcnn_x101_64x4d_fpn_1x.py

srun -p dsta --mpi=pmi2 --gres=gpu:1 -n1 --ntasks-per-node=1 --job-name=job --kill-on-bad-exit=1 -w SG-IDC1-10-51-2-73 \
python -m pdb -c continue tools/test.py \
    configs/scene_graph/VG_PredCls_motif_mask_X_rcnn_x101_64x4d_fpn_1x.py \
    new_experiments/VG_PredCls_motif_mask_X_rcnn_x101_64x4d_fpn_1x/latest.pth \
    --eval predcls \
    --relation_mode \
    --show
    # --out new_experiments/VG_PredCls_motif_mask_X_rcnn_x101_64x4d_fpn_1x/latest.pth \
