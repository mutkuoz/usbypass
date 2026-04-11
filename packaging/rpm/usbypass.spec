%global pypi_name usbypass
%global debug_package %{nil}

Name:           %{pypi_name}
Version:        0.1.0
Release:        1%{?dist}
Summary:        Use a physical USB drive as a password-bypass key for Linux PAM

License:        MIT
URL:            https://github.com/mutkuoz/usbypass
Source0:        https://github.com/mutkuoz/usbypass/archive/v%{version}/%{name}-%{version}.tar.gz

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  pyproject-rpm-macros
BuildRequires:  systemd-rpm-macros

Requires:       python3 >= 3.9
Requires:       python3-pyudev
Requires:       systemd
Requires:       util-linux
Requires:       pam

%description
USBYPASS turns any USB mass-storage device into an authentication token
wired into PAM. When the enrolled key is plugged in, sudo and login skip
the password prompt. When it is absent, the system behaves like a
perfectly ordinary, password-protected Linux box.

Anti-clone protection binds the stored handshake to the physical USB
controller's serial, so dd-ing the drive to another stick will not
produce a working clone. Password authentication always remains
available as a fallback.

This is a convenience layer over PAM. It is NOT two-factor
authentication, and it does not protect data at rest — combine it with
LUKS / dm-crypt for real confidentiality.

%prep
%autosetup -n %{name}-%{version}

%generate_buildrequires
%pyproject_buildrequires

%build
%pyproject_wheel

%install
%pyproject_install
%pyproject_save_files %{pypi_name}

# CLI shim and PAM/udev helpers into /usr/bin and /usr/libexec.
install -D -m 0755 scripts/usbypass              %{buildroot}%{_bindir}/usbypass
install -D -m 0755 scripts/usbypass-pam-helper   %{buildroot}%{_libexecdir}/usbypass-pam-helper
install -D -m 0755 scripts/usbypass-udev-handler %{buildroot}%{_libexecdir}/usbypass-udev-handler

# Rewrite the shebang/path in the udev helper and pam helper so they
# point into /usr/libexec rather than /usr/local/libexec (which is where
# the in-repo install.sh drops them).
install -D -m 0644 packaging/rpm/99-usbypass.rules \
    %{buildroot}%{_udevrulesdir}/99-usbypass.rules

# systemd unit.
install -D -m 0644 systemd/usbypass-clear-sudo.service \
    %{buildroot}%{_unitdir}/usbypass-clear-sudo.service

# State directories — owned by this package so rpm tracks them.
install -d -m 0700 %{buildroot}%{_sysconfdir}/usbypass
install -d -m 0755 %{buildroot}%{_sharedstatedir}/usbypass

# /run/usbypass is created at runtime by state.py — nothing to ship.

%post
# Generate the host secret on first install (idempotent).
if [ ! -s %{_sysconfdir}/usbypass/secret.key ]; then
    %{__python3} -c 'from usbypass import crypto; crypto.generate_secret()' || :
fi
/usr/bin/udevadm control --reload-rules || :
/usr/bin/udevadm trigger --subsystem-match=block --action=change || :
%systemd_post usbypass-clear-sudo.service
# Install the PAM hook via our own installer (marker-bracketed edit on
# Fedora because there is no pam-auth-update equivalent).
%{__python3} -m usbypass install --pam-only || :

%preun
%systemd_preun usbypass-clear-sudo.service
if [ "$1" = 0 ]; then
    %{__python3} -m usbypass uninstall || :
fi

%postun
%systemd_postun_with_restart usbypass-clear-sudo.service
/usr/bin/udevadm control --reload-rules || :

%files -f %{pyproject_files}
%license LICENSE
%doc README.md docs/
%{_bindir}/usbypass
%{_libexecdir}/usbypass-pam-helper
%{_libexecdir}/usbypass-udev-handler
%{_udevrulesdir}/99-usbypass.rules
%{_unitdir}/usbypass-clear-sudo.service
%dir %attr(0700, root, root) %{_sysconfdir}/usbypass
%dir %attr(0755, root, root) %{_sharedstatedir}/usbypass

%changelog
* Sun Apr 12 2026 USBYPASS contributors <noreply@example.com> - 0.1.0-1
- Initial RPM release.
