#!/usr/bin/env python3 -u
# Copyright (c) Facebook, Inc. and its affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

import argparse
import math
import os
import os.path as osp
import tqdm
import torch
import torch.nn.functional as F
from shutil import copyfile

from npy_append_array import NpyAppendArray

import fairseq
import soundfile as sf


def get_parser():
    parser = argparse.ArgumentParser(
        description="compute kmeans codebook from kaldi-computed feats"
    )
    # fmt: off
    parser.add_argument('data', help='location of tsv files')
    parser.add_argument('--split', help='which split to read', required=True)
    parser.add_argument('--save-dir', help='where to save the output', required=True)
    parser.add_argument('--checkpoint', type=str, help='checkpoint for wav2vec ctc model', required=True)
    parser.add_argument('--layer', type=int, default=14, help='which layer to use')
    # fmt: on

    return parser


class Wav2VecFeatureReader(object):
    def __init__(self, cp_file, layer):
        # overrides to make code working
        path, checkpoint = os.path.split(cp_file)
        overrides = {
            "task": "audio_finetuning",
            "w2v_path": path
        }
        state = fairseq.checkpoint_utils.load_checkpoint_to_cpu(cp_file, arg_overrides=overrides)

        if "cfg" in state:
            w2v_args = state["cfg"]
            w2v_args["task"]["data"] = "/home/marten/MA/data/models/"
            task = fairseq.tasks.setup_task(w2v_args.task)
            model = task.build_model(w2v_args.model)
        else:
            w2v_args = state["args"]
            task = fairseq.tasks.setup_task(w2v_args)
            model = task.build_model(w2v_args)
        model.load_state_dict(state["model"], strict=True)
        model.eval()
        model.cuda()
        self.model = model
        self.task = task
        self.layer = layer

    def read_audio(self, fname):
        """Load an audio file and return PCM along with the sample rate"""
        wav, sr = sf.read(fname)
        assert sr == 16e3

        return wav

    def get_feats(self, loc):
        x = self.read_audio(loc)
        with torch.no_grad():
            source = torch.from_numpy(x).float().cuda()
            if self.task.cfg.normalize:
                assert source.dim() == 1, source.dim()
                with torch.no_grad():
                    source = F.layer_norm(source, source.shape)
            source = source.view(1, -1)

            m_res = self.model(source=source, padding_mask=None)
            return m_res["layer_results"][math.floor(self.layer / 2)][(self.layer % 2) * 2].squeeze(1).cpu()


def get_iterator(args):
    with open(osp.join(args.data, args.split) + ".tsv", "r") as fp:
        lines = fp.read().split("\n")
        root = lines.pop(0).strip()
        files = [osp.join(root, line.split("\t")[0]) for line in lines if len(line) > 0]

        num = len(files)
        reader = Wav2VecFeatureReader(args.checkpoint, args.layer)

        def iterate():
            for fname in files:
                w2v_feats = reader.get_feats(fname)
                yield w2v_feats

    return iterate, num


def main():
    parser = get_parser()
    args = parser.parse_args()

    os.makedirs(args.save_dir, exist_ok=True)

    def create_files(dest):
        copyfile(osp.join(args.data, args.split) + ".tsv", dest + ".tsv")
        if osp.exists(osp.join(args.data, args.split) + ".wrd"):
            copyfile(osp.join(args.data, args.split) + ".wrd", dest + ".wrd")
        if osp.exists(osp.join(args.data, args.split) + ".phn"):
            copyfile(osp.join(args.data, args.split) + ".phn", dest + ".phn")

        if osp.exists(dest + ".npy"):
            os.remove(dest + ".npy")
        npaa = NpyAppendArray(dest + ".npy")
        return npaa

    save_path = osp.join(args.save_dir, args.split)
    npaa = create_files(save_path)

    generator, num = get_iterator(args)
    iterator = generator()

    with open(save_path + ".lengths", "w") as l_f:
        for w2v_feats in tqdm.tqdm(iterator, total=num):
            print(len(w2v_feats), file=l_f)

            if len(w2v_feats) > 0:
                npaa.append(w2v_feats.numpy())


if __name__ == "__main__":
    main()
