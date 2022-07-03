# Sotoki

*Stack Overflow to Kiwix*

# This branched implementation can actually build a stackoverflow zim file. It's not pretty, but it works.
- Build it: `podman build -t sotoki:local . -f Dockerfile`
- Run it: `podman run --detach --name sotoki --shm-size 1g --security-opt label=disable --replace -v /work:/work:rw sotoki:local bash -c 'sotoki stackoverflow.com Kiwix --no-userprofile --threads="16" --no-identicons --nozim && zimwriterfs --language=eng --title="Stack Overflow" --description="Where Developers Learn, Share, & Build Careers" --source=https://stackoverflow.com --scraper=sotoki-1.3.2-dev0 --welcome=index.html --favicon=favicon.png --creator="Stack Overflow" --publisher=JM --tags="_category:stack_exchange;stackexchange;stackoverflow" --verbose --uniqueNamespace --zstd --redirects=/work/stackoverflow_com/redirection.csv /work/stackoverflow_com/output /work/stackoverflow.com_en_all.zim'`

# Things to know:
- sotoki takes about 24 hours to download and convert data when using an existing cache of images
- Without an existing image cache it could take days to scrape all the images
- zimwriterfs takes about 16 hours
- 32GB of RAM is a must, maybe even 64GB
- 700GB is close to minimum free space
- sotoki disk intensive. A good NVME disk will help a lot with performance. It's probably borderline unusable on a spinning disk.
- The output/questions and output/tag directories are difficult to delete because of their gargantuan size. One way is to use rsync, for example `rsync -r --delete emptydir questions`

The goal of this project is to create a suite of tools to create
[zim](https://openzim.org) files required by
[kiwix](https://kiwix.org/) reader to make available [Stack Overflow](https://stackoverflow.com/)
offline (without access to Internet). This use stackexchange dump from [Stack Exchange Data Dump](https://archive.org/details/stackexchange)

[![PyPI](https://img.shields.io/pypi/v/sotoki.svg)](https://pypi.python.org/pypi/sotoki)
[![Docker Build Status](https://img.shields.io/docker/build/openzim/sotoki)](https://hub.docker.com/r/openzim/sotoki)
[![CodeFactor](https://www.codefactor.io/repository/github/openzim/sotoki/badge)](https://www.codefactor.io/repository/github/openzim/sotoki)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

## Getting started

The use of btrfs as a file system is recommended (and required for stackoverflow)

Install non python dependencies:
```bash
sudo apt-get install jpegoptim pngquant gifsicle advancecomp python-pip python-virtualenv python-dev libxml2-dev libxslt1-dev libbz2-dev p7zip-full python-pillow gif2apng imagemagick
```

Create a virtual environment for python:
```bash
virtualenv --system-site-packages -p python3 ./
```

Activate the virtual enviroment:
```bash
source ./bin/activate
```

Install this lib:
```bash
pip3 install sotoki
```

Usage:
```bash
sotoki <domain> <publisher> [--directory=<dir>] [--nozim] [--tag-depth=<tag_depth>] [--threads=<threads>] [--zimpath=<zimpath>] [--reset] [--reset-images] [--clean-previous] [--nofulltextindex] [--ignoreoldsite] [--nopic] [--no-userprofile]
```

You can use `sotoki -h` to have more explanation about these options

## Example

```bash
for S in `./list_all.sh`
do
  sotoki $S Kiwix --threads=12 --reset --clean-previous --no-userprofile
done
```

## License

[GPLv3](https://www.gnu.org/licenses/gpl-3.0) or later, see
[LICENSE](LICENSE) for more details.
