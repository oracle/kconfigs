# Contributing to kconfigs

We welcome your contributions! There are multiple ways to contribute.
If you'd like to add a distribution kernel to this tool, there is specific
guidance under "Adding a Distribution" which you can reference once you've set
up your development environment.

## Opening issues

For bugs or enhancement requests, please file a GitHub issue unless it's
security related. When filing a bug remember that the better written the bug is,
the more likely it is to be fixed. If you think you've found a security
vulnerability, do not raise a GitHub issue and follow the instructions in our
[security policy](./SECURITY.md).

## Contributing code

### Oracle Contributor Agreement

We welcome your code contributions. Before submitting code via a pull request,
you will need to have signed the [Oracle Contributor Agreement][OCA] (OCA) and
your commits need to include the following line using the name and e-mail
address you used to sign the OCA:

```text
Signed-off-by: Your Name <you@example.org>
```

This can be automatically added to pull requests by committing with `--sign-off`
or `-s`, e.g.

```text
git commit --signoff
```

Only pull requests from committers that can be verified as having signed the OCA
can be accepted.

### Development Environment

Follow the "How to Run" section of the README first. Then, setup the development
environment and commit hooks:

```sh
make dev
```

This will install static analysis tools which run when you commit code. They
will run type checks, catch basic bugs, and ensure that you've formatted your
code according to the standard.

See the "Guide to the Code" section below for some help diving in.

## Pull request process

1. Fork this repository.
1. Create a branch in your fork to implement the changes. If your branch
   addresses an issue, it's good to reference the issue number in the branch
   name.
1. Ensure that any documentation is updated with the changes that are required
   by your change.
1. Submit the pull request. *Do not leave the pull request blank*. Explain exactly
   what your changes are meant to do and provide simple steps on how to validate.
   your changes. Ensure that you reference the issue you created as well.
1. We will review the pull request before it is merged.

## Code of conduct

Follow the [Golden Rule](https://en.wikipedia.org/wiki/Golden_Rule). If you'd
like more specific guidelines, see the [Contributor Covenant Code of Conduct][COC].

[OCA]: https://oca.opensource.oracle.com
[COC]: https://www.contributor-covenant.org/version/1/4/code-of-conduct/

## Adding a Distribution

If you would like to add a Dpkg, Rpm, or Pacman based distribution to kconfigs,
then the code is already written! You simply need to update `config.ini` with a
new section for the configuration. Here is a reference for the configuration
file:


```ini
# This section name should be brief, unique, and contain no spaces
[distro_x86_64]

# Name and version refer to the Linux distribution. The version is a string and
# it cound include codenames or other useful info. Mainly used for readers on the
# webpage.
name = Distribution Name
version = 1

# Architecture: only x86_64 and aarch64 are valid options so far.
# Note that some distros may use "amd64" or "arm64" (e.g. Ubuntu and Debian).
# The convention in this repository is to use x86_64 or aarch64, we just convert
# the names to what the distro expects in the code.
arch = x86_64

# The base name of the package containing the kernel. Typically this is "kernel"
# or "linux" or something similar.
package = kernel

# This references a file within the directory "gpg-keys" containing the package
# signing public key.
key = GPG-KEY-NAME

# This is the base URL of the package index for the distribution. The exact
# meaning will depend on which distribution you're using.
index = https://yum.example.com/version1/x86_64/

# These refer to Python classes that implement the core functionality: indexes
# check repository metadata and resolve kernel package artifacts, and extractors
# extract a config from a downloaded package. The configuration key is still
# named "fetcher" for compatibility, but it must point to an Index class.
fetcher = INDEX HERE
extractor = EXTRACTOR HERE
```

All distributions with package signing MUST be configured with their GPG key.
The index URL should use HTTPS but HTTP is fine if a GPG key is present, and the
repository metadata is signed.

To create the GPG key, download the distribution's key in ASCII format, add the
file to `gpg-keys`. Then update `gpg-keys/Makefile` to build a binary keychain
based on it.

Each distribution may require some special keys. These are documented below. Be
sure to also read through similar distributions within `config.ini` and use them
as a reference.

### RPM

Use the following:

```ini
fetcher = kconfigs.rpm.RpmIndex
extractor = kconfigs.rpm.RpmExtractor
```

### Dpkg

Use:

```ini
fetcher = kconfigs.deb.DebIndex
extractor = kconfigs.deb.DebExtractor
codename = release codename
package = linux-FLAVOR
```

The codename is included into the index URL, it references the release nickname
such as "Jammy" or "Trixie".

Note that Debian kernel packages are a bit confusing, there are typically
several kernel "flavors", which have associated packages. For example, Ubuntu
typically has "linux-generic", and then there are sub-packages like
"linux-image-generic" and "linux-modules-generic", and then these packages end
up depending on "linux-modules-$UNAME-generic", which is a specific kernel
version. We have some rather strange, but functional code to handle this mess in
Debian and Ubuntu, but it may not extend to other distributions.

So, set the package to `linux-FLAVOR`, replacing the flavor with the correct
value. If this does not work, file a Github issue with the distribution details
and we'll try to sort it out.

### Pacman

Use:

```ini
fetcher = kconfigs.pacman.PacmanIndex
extractor = kconfigs.pacman.PacmanExtractor
repo = core
```

Note that the index URL also includes the repo name (typically "core"). See the
existing configurations for further reference.

## Adding Other Kinds of Distributions

If you want to add a distro which uses some other package format, you'll need to
implement an index and/or extractor. This section gives you an idea of the
architecture of the code. See `kconfigs/index.py`, `kconfigs/model.py`, and
`kconfigs/main.py` for the APIs and orchestration.

### Indexes and Artifacts

Indexes understand repository metadata well enough to find the latest kernel
package for one or more configured distro targets. An index has three phases:

1. `index_id()` takes a distribution configuration, and returns the unique
   identifier for this index. Distro targets with the same index ID will share
   the index, avoiding duplicated work.
1. `check()` performs the smallest useful metadata operation and returns an
   `IndexState`.
1. `resolve(dc)` uses the checked state and any larger package metadata to
   return an `Artifact` for one `DistroConfig`.

As a concrete example, consider the RPM index:

1. The `index_id()` for RPM is the repository URL. (For simplicity, it also
   includes the GPG key used to verify packages from the URL).
1. The `check()` method fetches the `repomd.xml` file and checks to see what the
   latest database version is. The `IndexState` is the database URL (which
   includes the version in it).
1. The `resolve()` method downloads the database and queries for the matching
   kernel package. It returns an `Artifact` describing that kernel URL.

The returned `Artifact` contains the package URL, optional checksum, optional
detached signature URL, optional package version, and the `IndexState` it was
resolved from. `Artifact` objects just describe the downloads; they do not
download or extract anything themselves.

`IndexState` should be small, immutable, and JSON-serializable. If it matches
the `IndexState` from the previous artifact, then we know that the latest
artifact has not changed. It's just a convenient way to avoid doing extra work.

Since multiple distributions could share the same index, it's important for
`check()` and `resolve()` to be able to be called concurrently and share their
work. For this purpose, the `@alru_cache` helper can be used to ensure that
downloads or extractions only happen once, even when called multiple times or
concurrently.

To avoid redoing work, kconfigs maintains caches. First is the `state.json`,
which contains the latest artifact for each distribution. Since the artifact
contains the `IndexState` it was derived from, many distributions can be skipped
when no new package is available.

### Extractors

Once a package is downloaded, we need to know how to get the kernel
configuration out of it. This includes the logic to extract the contents of a
package, as well as knowledge of what FS location the config is stored at.
Sometimes, the "extract-ikconfig" script from the kernel may be used.
