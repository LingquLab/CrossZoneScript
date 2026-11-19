def stage_single_round(case, manifest, checksum):
    import hashlib
    import json
    from pathlib import Path
    import shutil

    product = Path('/work/results/v310-native-64p-single-round-build-20261119-01/tilexr_ep_dispatch_memory_kernel.o')
    baseline = manifest['kernel_sha256']
    assert checksum != baseline and hashlib.sha256(product.read_bytes()).hexdigest() == checksum
    binary = case/product.name
    shutil.copy2(product, binary)
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == checksum
    variant = {'schedule': 'SINGLE_ROUND', 'send_groups': 1, 'baseline_kernel_sha256': baseline}
    manifest.update(kernel_sha256=checksum, kernel_size_bytes=binary.stat().st_size,
                    simulation_variant=variant)
    (case/'manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    return variant
