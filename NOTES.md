# `VisualGenomeDataset` Format
`dict`
- `img_meta`
- `img`
- `gt_bboxes`: `Tensor[N_b, 4]`
- `gt_labels`: `Tensor[N_b]`
- `gt_rels`: `Tensor[N_r, 3]`
    - (s_idx, o_idx, pred_id)
- `gt_relmaps`: `Tensor[N_b, N_b]`
    - Matrix of pairwise relations
