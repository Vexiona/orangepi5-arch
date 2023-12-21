# Archlinux for Orangepi 5 / 5B / 5 plus

This repo targets Arch Linux as a build platform rather than Ubuntu.

## Credits

The above code is a combination of [7Ji/orangepi5-archlinuxarm](https://github.com/7Ji/orangepi5-archlinuxarm) and [7Ji/orangepi5-rkloader](https://github.com/7Ji/orangepi5-rkloader). I rewrote it in python because I can't bash for the life of me.

## Dependencies

- python2
- python3.12 (anything less will not work)
  - setuptools
  - pyelftools
  - gitpython
- mtools (for mcopy)

## TODO

Port *build-arch-child.sh* to python as well.
