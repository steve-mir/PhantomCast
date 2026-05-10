Prerequisites bundled with the installer
========================================

VC_redist.x64.exe
    Microsoft Visual C++ 2015-2022 Redistributable (x64).
    Download fresh build before each release from:
        https://aka.ms/vs/17/release/vc_redist.x64.exe
    The installer runs this only if the runtime is missing.

NVIDIA Driver
    The end user supplies their own NVIDIA driver. NVIDIA's EULA forbids
    driver redistribution. Our docs (https://phantomcast.space/install)
    point users at https://www.nvidia.com/Download/index.aspx with a
    minimum version of 525.60 for CUDA 12.x support.

CUDA 12.8 / cuDNN 8.9.7
    *Bundled* inside the PyInstaller payload (dist/PhantomCast/_runtime/cuda/bin
    and torch/lib + nvidia/*/bin). Users do NOT need to install these.
    See build/PhantomCast.spec for the collection logic.
