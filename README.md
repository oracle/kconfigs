# kconfigs

This is a system for fetching the Linux kernel configuration files for a variety
of Linux distributions, and archiving them for later analysis. The resulting
configuration data is summarized into a compact JSON file which can be
browsed on [our webpage][1]. Or, you can simply download the configurations and
explore them with `grep`.

[1]: https://oracle.github.io/kconfigs/

On a regular basis, Github CI runs will fetch more recent package versions and
keep the configurations up-to-date.

## Browsing Configurations Locally

While the [webpage][1] is convenient, you can also directly clone the `gh-pages`
branch of this repository to explore locally:

``` sh
git clone https://github.com/oracle/kconfigs -b gh-pages
cd kconfigs
grep -r IKCONFIG_PROC out/
```

## How to Run

This tool runs on Python 3.11 or later. It requires common compression tools
(gzip, bzip2, xz, zstd, tar) as well as common command line tools for Linux
packaging installed to the system (gpg, rpm, cpio, dpkg).

The following setup instructions apply to Oracle Linux 9. First, install runtime
dependencies (most should already be installed, but they are listed for
completeness).

```sh
dnf install -y python3.11{,-devel,-venv,-pip} \
               gzip bzip2 xz zstd tar \
               rpm cpio dpkg \
               make
```

To setup the runtime Python environment:

``` sh
git clone https://github.com/oracle/kconfigs
cd kconfigs
make venv
```

To run the extraction routine:

``` sh
make run
```

Configurations will appear in the `out` directory.

## Documentation

You should be able to find everything you need by browsing to our [web page][1]
which displays the latest kernel configuration information.

## Examples

See "How to Run".

## Help

If you have any questions or concerns, please open a Github issue and we will
try to help you out.

## Contributing

This project welcomes contributions from the community. Before submitting a pull
request, please [review our contribution guide](./CONTRIBUTING.md).

## Security

The kconfigs project tries to verify all GPG and checksum metadata for the
sources it uses. Here's an overview of what we check:

* RPM-based distributions:
  * A GPG key is required for all RPM-based distributions.
  * `repomd.xml`: GPG signature required & checked. We do have an exception for
    some unsigned databases that are served via HTTPS from an allowlist of
    trusted hosts.
  * Sqlite database: checksum required & checked.
  * RPM package: checksum required & checked, RPM's built-in GPG signature is
    also required and checked.
* Debian-based distributions:
  * A GPG key is required for all Debian-based distributions.
  * `Release` file: GPG signature required & checked.
  * `Packages` file: checksum required & checked.
* Pacman-based distributions:
  * The database file is not signed and cannot be verified.
  * The individual packages are GPG signed, which we verify.
* Upstream kernel configurations:
  * We verify that stable kernel releases have a valid signature from Greg KH.
  * The mainline source distribution is unsigned and cannot be verified.
* Android configurations:
  * We rely on HTTPS connections to `source.android.com` to ensure the integrity
    of the data. There are no GPG signatures or checksums provided.

Please consult the [security guide](./SECURITY.md) for our responsible security
vulnerability disclosure process

## License

Copyright (c) 2024, Oracle and/or its affiliates.

Licensed under the terms of the GNU General Public License.
