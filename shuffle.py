import itertools
import random
import sys

import docker


def iter_shuffle(lst):
    while True:
        ls = list(lst)
        random.shuffle(ls)
        for x in ls:
            yield x


def _is_ideep_supported(python_version):
    # https://github.com/intel/ideep#requirements
    pyver = python_version
    if pyver[0] == 2:
        return pyver >= (2, 7, 6)
    assert pyver[0] == 3
    if pyver[:2] == (3, 5):
        return pyver >= (3, 5, 2)
    if pyver[:2] == (3, 6):
        return True
    if pyver[:2] == (3, 7):
        return True
    return False


def get_shuffle_params(params, index):
    random.seed(index)
    keys = sorted(params.keys())
    iters = [iter_shuffle(params[key]) for key in keys]

    while True:
        vals = tuple(next(iter) for iter in iters)
        ret = dict(zip(keys, vals))
        valid, reason = _is_shuffle_params_valid(ret)
        if valid:
            return ret
        print('Skipping invalid shuffle combination ({}): {}'.format(reason, ret))


def _is_shuffle_params_valid(ret):
    base = ret['base']

    # avoid SEGV
    if ret['numpy'] == '1.9' and ret.get('h5py'):
        return False, 'NumPy 1.9 incompatible with h5py'

    py_ver = docker.get_python_version(base)

    # Avoid unsupported NumPy/SciPy version for the Python version.
    if py_ver[:2] == (3, 5):
        # Python 3.5 is first supported in NumPy 1.11.
        if ret['numpy'] in ['1.9', '1.10']:
            return False, 'NumPy version does not support Python 3.5'
    elif py_ver[:2] == (3, 6):
        # Python 3.6 is first supported in NumPy 1.12.
        if ret['numpy'] in ['1.9', '1.10', '1.11']:
            return False, 'NumPy version does not support Python 3.6'
        # Python 3.6 is first supported in SciPy 0.19.
        if ret.get('scipy', None) in ['0.18']:
            return False, 'SciPy version does not support Python 3.7'
    elif py_ver[:2] == (3, 7):
        # Python 3.7 is first supported in NumPy 1.14.4.
        if ret['numpy'] in ['1.9', '1.10', '1.11', '1.12', '1.13']:
            return False, 'NumPy version does not support Python 3.7'
        # Python 3.7 is first supported in SciPy 1.0.
        if ret.get('scipy', None) in ['0.18', '0.19']:
            return False, 'SciPy version does not support Python 3.7'

    # iDeep requirements:
    # - Ubuntu 16.04 or CentOS 7.4 or OS X
    # - NumPy 1.13.0+ with Python 2.7/3.5/3.6
    # - NumPy 1.16.0+ with Python 3.7+
    if ret.get('ideep'):
        if (('centos6' in base or 'ubuntu14' in base) or
                not _is_ideep_supported(py_ver)):
            return False, 'iDeep not supported on {}'.format(base)
        elif py_ver[:2] >= (3, 7):
            if ret['numpy'] in ['1.9', '1.10', '1.11', '1.12', '1.13', '1.14', '1.15']:
                return False, 'iDeep not supported on this Python/NumPy combination'
        else:
            if ret['numpy'] in ['1.9', '1.10', '1.11', '1.12']:
                return False, 'iDeep not supported on this NumPy version'

    # SciPy 0.19 installation from source (--no-binary) fails with new NumPy 1.16+.
    # Theano 1.0.3 or earlier does not support NumPy 1.16+.
    if ret['numpy'] not in ['1.9', '1.10', '1.11', '1.12', '1.13', '1.14', '1.15']:
        if ret.get('scipy', None) in ['0.18', '0.19']:
            return False, 'SciPy version does not support this NumPy version'
        if ret.get('theano') in ['0.8', '0.9']:
            return False, 'Theano version does not support this NumPy version'

    if 'centos6' in base and ret.get('protobuf') == 'cpp-3':
        return False, 'protobuf cpp-3 not supported on centos6'

    cuda, cudnn, nccl = ret['cuda_cudnn_nccl']

    if 'centos6' in base and nccl != 'none':
        # https://docs.nvidia.com/deeplearning/sdk/nccl-install-guide/index.html#rhel_centos
        return False, 'NCCL is not supported in centos6'

    if (cuda == 'cuda80' and
            not any(base.startswith(x) for x in ['ubuntu14', 'ubuntu16', 'centos6', 'centos7'])):
        # https://docs.nvidia.com/cuda/archive/8.0/cuda-installation-guide-linux/index.html
        return False, 'CUDA 8.0 is not supported on {}'.format(base)
    elif (cuda in ['cuda90', 'cuda91', 'cuda92'] and
            not any(base.startswith(x) for x in ['ubuntu16', 'centos6', 'centos7'])):
        # https://docs.nvidia.com/cuda/archive/9.0/cuda-installation-guide-linux/index.html
        # https://docs.nvidia.com/cuda/archive/9.1/cuda-installation-guide-linux/index.html
        # https://docs.nvidia.com/cuda/archive/9.2/cuda-installation-guide-linux/index.html
        return False, 'CUDA 9.x is not supported on {}'.format(base)
    elif (cuda in ['cuda100', 'cuda101'] and
            not any(base.startswith(x) for x in ['ubuntu14', 'ubuntu16', 'ubuntu18', 'centos6', 'centos7'])):
        # https://docs.nvidia.com/cuda/archive/10.0/cuda-installation-guide-linux/index.html
        # https://docs.nvidia.com/cuda/archive/10.1/cuda-installation-guide-linux/index.html
        return False, 'CUDA 10.x is not supported on {}'.format(base)

    return True, None


def parse_version(version):
    """Parse a version number to make an int list."""
    return [int(num) for num in version.split('.')]


def make_require(name, version):
    version_number = parse_version(version)
    version_number[-1] += 1
    next_ver = '.'.join(str(num) for num in version_number)
    return '%s<%s' % (name, next_ver)


def append_require(params, conf, name):
    version = params.get(name)
    if version:
        overwrite_requires_version(conf, name, make_require(name, version))


def overwrite_requires_version(conf, name, requirement):
    # Overwrite the requirements for the package `name` already
    # defined in `requires`.
    _, conf['requires'] = docker.partition_requirements(name, conf['requires'])
    conf['requires'].append(requirement)


def make_conf(params):
    conf = {
        'requires': [],
    }

    if 'base' in params:
        conf['base'] = params['base']
    if 'cuda_cudnn_nccl' in params:
        conf['cuda'], conf['cudnn'], conf['nccl'] = params['cuda_cudnn_nccl']

    append_require(params, conf, 'setuptools')
    append_require(params, conf, 'pip')
    append_require(params, conf, 'cython')
    append_require(params, conf, 'numpy')
    append_require(params, conf, 'scipy')

    # Note: h5py 2.5 uses NumPy in its setup script, so NumPy needs to be
    # installed before h5py.
    append_require(params, conf, 'h5py')

    append_require(params, conf, 'theano')

    if params.get('protobuf') == 'cpp-3':
        conf['protobuf-cpp'] = 'protobuf-cpp-3'
    else:
        append_require(params, conf, 'protobuf')

    ideep = params.get('ideep')
    if ideep is not None:
        overwrite_requires_version(
            conf, 'ideep4py', make_require('ideep4py', ideep))

    append_require(params, conf, 'pillow')

    if params.get('wheel') is True:
        conf['requires'].append('wheel')

    return conf


def make_shuffle_conf(params, index):
    params = get_shuffle_params(params, index)

    print('--- Shuffle Parameters ---')
    for key, value in params.items():
        print('{}: {}'.format(key, value))
    sys.stdout.flush()

    conf = make_conf(params)

    print('--- Configuration ---')
    for key, value in conf.items():
        print('{}: {}'.format(key, value))
    sys.stdout.flush()

    return conf
