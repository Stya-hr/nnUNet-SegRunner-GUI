import os
import sys
import time
import argparse


def list_input_files(in_dir: str):
    files = []
    for name in sorted(os.listdir(in_dir)):
        lower = name.lower()
        if lower.endswith('.nii') or lower.endswith('.nii.gz'):
            files.append(os.path.join(in_dir, name))
    return files


def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)


def write_fake_nifti(path: str):
    # 仅生成一个占位文件，满足后续流程扫描与复制
    ensure_dir(os.path.dirname(path))
    with open(path, 'wb') as f:
        f.write(b'FAKE_NIFTI_DATA')


def simulate_progress(total_steps: int = 20, sleep_sec: float = 0.05):
    for i in range(total_steps + 1):
        pct = int(i * 100 / total_steps)
        msg = f"{pct}%|########| step {i}/{total_steps}"
        print(msg, end='\r', flush=True)
        time.sleep(sleep_sec)
    print("\nDone")


def run_case_mode(in_dir: str, out_dir: str):
    # 单例模式：输出一个固定文件名，供上层服务查找
    simulate_progress()
    out_path = os.path.join(out_dir, 'prediction.nii.gz')
    write_fake_nifti(out_path)


def run_batch_mode(in_dir: str, out_dir: str):
    files = list_input_files(in_dir)
    n = len(files)
    if n == 0:
        # 也模拟一下进度输出
        simulate_progress(5, 0.03)
        return
    for idx, fp in enumerate(files, start=1):
        # 针对每个文件做一个小进度
        for i in range(0, 101, 20):
            print(f"{i}% processing {idx}/{n}", end='\r', flush=True)
            time.sleep(0.03)
        base = os.path.basename(fp)
        out_name = os.path.splitext(os.path.splitext(base)[0])[0] if base.lower().endswith('.nii.gz') else os.path.splitext(base)[0]
        out_path = os.path.join(out_dir, out_name + '.nii.gz')
        write_fake_nifti(out_path)
    print("\nBatch Done")


def main():
    parser = argparse.ArgumentParser(description='Mock nnUNetv2_predict')
    parser.add_argument('-i', '--input', dest='in_dir', required=True)
    parser.add_argument('-o', '--output', dest='out_dir', required=True)
    parser.add_argument('-d', '--dataset', dest='dataset', required=False, default='101')
    parser.add_argument('-c', '--config', dest='config', required=False, default='3d_fullres')
    parser.add_argument('-f', '--folds', dest='folds', required=False, default='0')
    args = parser.parse_args()

    in_dir = os.path.abspath(args.in_dir)
    out_dir = os.path.abspath(args.out_dir)
    ensure_dir(out_dir)

    files = list_input_files(in_dir)
    # Heuristic: 如果输入目录包含 case_0000.nii* 这类文件，认为是单例（per-case）模式
    is_case_mode = any(os.path.basename(p).startswith('case_') for p in files)

    if is_case_mode:
        run_case_mode(in_dir, out_dir)
    else:
        run_batch_mode(in_dir, out_dir)


if __name__ == '__main__':
    main()
