# Contributing to kconfigs

We welcome your contributions! There are multiple ways to contribute.

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
1. Create a branch in your fork to implement the changes. We recommend using
   the issue number as part of your branch name, e.g. `1234-fixes`.
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

## Guide to the Code

Each distribution has two important parts:

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
