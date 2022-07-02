FROM ubuntu:kinetic as builder

RUN apt-get update
RUN apt-get -y install aria2 \
                       automake \
                       autopoint \
                       build-essential \
                       ccache \
                       cmake \
                       curl \
                       default-jdk \
                       elfutils \
                       flatpak \
                       flatpak-builder \
                       fuse \
                       g++-multilib \
                       gcc \
                       gcc-multilib \
                       git \
                       libbz2-dev \
                       libc6-dev \
                       libc6-dev-i386 \
                       libdocopt-dev \
                       libfuse2 \
                       libgl-dev \
                       libgtest-dev \
                       libgumbo-dev \
                       liblzma-dev \
                       libmagic-dev \
                       libmicrohttpd-dev \
                       libtool \
                       libzstd-dev \
                       meson \
                       ninja-build \
                       openssh-client \
                       patch \
                       patchelf \
                       pkg-config \
                       python3-pip \
                       python3-setuptools \
                       python3-wheel \
                       subversion \
                       uuid-dev \
                       unzip \
                       wget \
                       zlib1g-dev

RUN git clone https://github.com/openzim/zim-tools
RUN git clone https://github.com/openzim/libzim

WORKDIR /libzim
COPY libzim.patch /libzim/libzim.patch
RUN git checkout br_6.3
RUN patch -p1 --ignore-whitespace < libzim.patch
RUN meson . build --default-library=static
RUN ninja -C build
RUN cd build && meson install

WORKDIR /zim-tools
COPY zim-tools.patch /zim-tools/zim-tools.patch
RUN git checkout 2.1.0
RUN patch -p1 --ignore-whitespace < zim-tools.patch
RUN meson . build -Dstatic-linkage=true
### BEGIN DUNNO ###
RUN rm /usr/lib/x86_64-linux-gnu/libdocopt.so.0.6.1
RUN ln -sf /usr/lib/x86_64-linux-gnu/libdocopt.a /usr/lib/x86_64-linux-gnu/libdocopt.so.0.6.1
RUN sed -i 's,/usr/lib/x86_64-linux-gnu/libz.a,/usr/lib/x86_64-linux-gnu/libz.a /usr/lib/x86_64-linux-gnu/libbz2.a,g' build/build.ninja 
### END DUNNO ###
RUN ninja -C build

FROM python:3.11-rc
COPY --from=builder /zim-tools/build/src/zimwriterfs/zimwriterfs /usr/local/bin/zimwriterfs

# Install necessary packages
RUN apt-get update -y \
 && apt-get install -y --no-install-recommends \
      advancecomp \
      gif2apng \
      imagemagick \
      libbz2-dev \
      libjpeg-dev \
      libpng-dev \
      libxml2-dev \
      libxslt1-dev \
      locales \
      p7zip-full \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Install jpegoptim
RUN wget http://www.kokkonen.net/tjko/src/jpegoptim-1.4.6.tar.gz \
 && tar xvf jpegoptim-1.4.6.tar.gz \
 && cd jpegoptim-1.4.6 \
 && ./configure \
 && make all install \
 && cd .. \
 && rm -rf jpegoptim-1.4.6*

# Install pngquant
RUN wget http://pngquant.org/pngquant-2.12.5-src.tar.gz \
 && tar xvf pngquant-2.12.5-src.tar.gz \
 && cd pngquant-2.12.5 \
 && ./configure \
 && make all install \
 && cd .. \
 && rm -rf pngquant-2.12.5*

# Install gifsicle
RUN wget https://www.lcdf.org/gifsicle/gifsicle-1.92.tar.gz \
 && tar xvf gifsicle-1.92.tar.gz \
 && cd gifsicle-1.92 \
 && ./configure \
 && make all install \
 && cd .. \
 && rm -rf gifsicle-1.92*

# Install libzim
ENV LIBZIM_VERSION 6.1.1
ENV LIBZIM_LIBRARY_PATH lib/x86_64-linux-gnu/libzim.so.$LIBZIM_VERSION
ENV LIBZIM_RELEASE libzim_linux-x86_64-$LIBZIM_VERSION
ENV LIBZIM_INCLUDE_PATH include/zim
RUN mkdir libzim \
 && cd libzim \
 && wget -qO- https://download.openzim.org/release/libzim/$LIBZIM_RELEASE.tar.gz | tar -xz -C . \
 && mv $LIBZIM_RELEASE/$LIBZIM_LIBRARY_PATH /usr/lib/libzim.so \
 && mv $LIBZIM_RELEASE/$LIBZIM_INCLUDE_PATH /usr/include/zim \
 && cd .. \
 && rm -rf libzim \
 && ldconfig

# Prepare python / pip
RUN locale-gen "en_US.UTF-8"
RUN /usr/local/bin/python -m pip install --upgrade pip

# Install python-libzim
RUN git clone https://github.com/openzim/python-libzim \
 && cd python-libzim \
 && git checkout v0.1 \
 && sed -i 's/cython ==/cython >=/g' pyproject.toml \
 && pip install ./ \
 && cd .. \
 && rm -rf python-libzim

# Install python-scraperlib
RUN git clone https://github.com/openzim/python-scraperlib \
 && cd python-scraperlib \
 && git checkout v1.3.6 \
 && sed -i 's/lxml.*/lxml>=4.9.0/g' requirements.txt \
 && pip install ./ \
 && cd .. \
 && rm -rf python-scraperlib

# Install sotoki
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install -r /tmp/requirements.txt
COPY . /app
WORKDIR /app
RUN python3 setup.py install
WORKDIR /
RUN rm -rf /app

# Boot commands
CMD sotoki ; /bin/bash
