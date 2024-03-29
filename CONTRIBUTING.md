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

Follow the "How to Run" section of the README first. Then, you can install
additional development dependencies:

```sh
dnf install -y pre-commit  # ensure EPEL is enabled
```

Then, setup the development environment and commit hooks:

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

# These refer to Python classes that implement the core functionality: "fetchers"
# can check for updates and fetch the kernel package, and "extractors" can take a
# kernel package and extract the kernel file from it. See kconfigs/fetcher.py and
# kconfigs/extractor.py for API details, and further info below.
fetcher = FETCHER HERE
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
fetcher = kconfigs.rpm.RpmFetcher
extractor = kconfigs.rpm.RpmExtractor
```

### Dpkg

Use:

```ini
fetcher = kconfigs.deb.DebFetcher
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
fetcher = kconfigs.pacman.PacmanFetcher
extractor = kconfigs.pacman.PacmanExtractor
repo = core
```

Note that the index URL also includes the repo name (typically "core"). See the
existing configurations for furthe reference.

## Adding Other Kinds of Distributions

If you want to add a distro which uses some other package format, you'll need to
implement a fetcher and/or extractor. This section gives you an idea of the
architecture of the code. See the corresponding files for the APIs you'll need
to implement, and see `kconfigs/main.py` for the code that actually uses it.

### Fetchers

Fetchers (see `kconfigs/fetcher.py`) understand the package manager's repository
metadata, at least well enough to check for the latest version of the kernel
package and get its URL, along with any available checksum and/or signature.
Each distribution has its own fetcher implementation.

Normally, package manager based fetchers have three parts:

1. Checking whether the package database has changed. This might be done by a
   small metadata file, or even by a HTTP head request to see the last modified
   time.
2. If the database is changed, then it needs to be fetched in order to check
   whether the kernel package has an update.
3. Finally, if the package is updated, then it needs to be downloaded.

When the program is run, we provide a directory where repository metadata can be
downloaded to. We also provide a `state.json` file where fetcher can cache
information. For instance, they could store the last modified time of the
database, or the previous checksum of the database, to use when checking for
updates.

### Extractors

Once a package is downloaded, we need to know how to get the kernel
configuration out of it. This includes the logic to extract the contents of a
package, as well as knowledge of what FS location the config is stored at.
Sometimes, the "extract-ikconfig" script from the kernel may be used.
