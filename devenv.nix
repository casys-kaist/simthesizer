{
  pkgs,
  lib,
  config,
  inputs,
  ...
}:

{
  languages.python = {
    enable = true;
    package = pkgs.python312;

    uv = {
      enable = true;
      sync.enable = true;
    };
  };

  languages.rust = {
    enable = true;
    channel = "stable";
  };

  packages = with pkgs; [
    cudaPackages_12_8.cudatoolkit
    rdma-core

    # LLMServingSim
    cmake
    gnumake
    pkg-config
    protobuf
    zlib
  ];

  env = {
    PROTOBUF_FROM_SOURCE = "True"; # LLMServingSim env
    VIRTUAL_ENV = ".devenv/state/venv";
    LD_LIBRARY_PATH = "/run/opengl-driver/lib";
    LIBRARY_PATH = "/run/opengl-driver/lib";
  };
}
