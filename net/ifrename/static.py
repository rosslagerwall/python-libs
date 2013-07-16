#!/usr/bin/env python

"""
Copyright (c) 2013, Citrix Inc.
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met: 

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer. 
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution. 

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

"""
Object for manipulating static rules.

Rules are of the form:
  <target name>: <id method> = "value"

target name must be in the form eth*
id methods are:
  mac: value should be the mac address of a device (e.g. DE:AD:C0:DE:00:00)
  pci: value should be the pci bus location of the device (e.g. 0000:01:01.1)
  ppn: value should be the result of the biosdevname physical naming policy of a device (e.g. p1p1)
  label: value should be the SMBios label of a device (for SMBios 2.6 or above)

Any line starting with '#' is considered to be a comment

"""

__version__ = "1.1.1"
__author__  = "Andrew Cooper"

import re

from xcp.logger import LOG
from xcp.net.mac import VALID_COLON_MAC as VALID_MAC
from xcp.net.ifrename.macpci import MACPCI
from xcp.pci import VALID_SBDF as VALID_PCI
from os.path import exists as pathexists

VALID_LINE = re.compile(
    r"^\s*(?P<target>eth\d+)"         # <target name>
    r"\s*(?::\s*(?P<method>[^=]+?))?" # Optional Colon <id method>
    r"\s*="                           # Equals
    r"\s*(?P<val>.+)$"                # "value" (quotes optional)
    )

SAVE_HEADER = """# Static rules.  Autogenerated by the installer from the answerfile or previous install
# WARNING - rules in this file override the 'lastboot' assignment of names,
#           so editing it may cause unexpected renaming on next boot

# Rules are of the form:
#   target name: id method = "value"

# target name must be in the form eth*
# id methods are:
#   mac: value should be the mac address of a device (e.g. DE:AD:C0:DE:00:00)
#   pci: value should be the pci bus location of the device (e.g. 0000:01:01.1)
#   ppn: value should be the result of the biosdevname physical naming policy of a device (e.g. p1p1)
#   label: value should be the SMBios label of a device (for SMBios 2.6 or above)

"""

class StaticRules(object):
    """
    Object for parsing the static rules configuration.

    There are two distinct usecases; the installer needs to write the
    static rules from scratch, whereas interface-rename.py in dom0 needs
    to read them.
    """

    methods = ["mac", "pci", "ppn", "label", "guess"]
    validators = { "mac": VALID_MAC,
                   "pci": VALID_PCI,
                   "ppn": re.compile("^(?:em\d+|p(?:ci)?\d+p\d+)$")
                   }

    def __init__(self, path=None, fd=None):

        self.path = path
        self.fd = fd
        self.formulae = {}
        self.rules = []

    def load_and_parse(self):
        """
        Parse the static rules file.
        Returns boolean indicating success or failure.
        """

        fd = None

        try:
            try:
                # If we were given a path, try opening and reading it
                if self.path:
                    if not pathexists(self.path):
                        LOG.error("Static rule file '%s' does not exist"
                                  % (self.path,))
                        return False
                    fd = open(self.path, "r")
                    raw_lines = fd.readlines()

                # else if we were given a file descriptor, just read it
                elif self.fd:
                    raw_lines = self.fd.readlines()

                # else there is nothing we can do
                else:
                    LOG.error("No source of data to parse")
                    return False

            except IOError, e:
                LOG.error("IOError while reading file: %s" % (e,))
                return False
        finally:
            # Ensure we alway close the file descriptor we opened
            if fd:
                fd.close()

        # Generator to strip blank lines and line comments
        lines = ( (n, l.strip()) for (n, l) in enumerate(raw_lines)
                  if (len(l.strip()) and l.strip()[0] != '#') )


        for num, line in lines:
            # Check the line is valid
            res = VALID_LINE.match(line)
            if res is None:
                LOG.warning("Unrecognised line '%s' in static rules (line %d)"
                            % (line, num))
                continue

            groups = res.groupdict()

            target = groups["target"].strip()

            if groups["method"] is None:
                # As method is optional, set to 'guess' if not present
                method = "guess"
                LOG.debug("Guessing method for interface %s on line %d"
                          % (target, num) )
            else:
                method = groups["method"].strip()

            value = groups["val"].strip()
            if value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
                # If we should guess the value, quotes imply a label
                if value == "guess":
                    value = "label"

            # Check that it is a recognised method
            if method not in StaticRules.methods:
                LOG.warning("Unrecognised static identification method "
                            "'%s' on line %d - Ignoring" % (method, num))
                continue

            # If we need to guess the method from the value
            if method == "guess":
                for k, v in StaticRules.validators.iteritems():
                    if v.match(value) is not None:
                        method = k
                        break
                else:
                    # If no validators, assume label
                    method = "label"

            # If we have a validator, test the valididy
            else:
                if method in StaticRules.validators:
                    if StaticRules.validators[method].match(value) is None:
                        LOG.warning("Invalid %s value '%s' on line %d - Ignoring"
                                    % (method, value, num))
                        continue

            # Warn if aliasing a previous static rule
            if target in self.formulae:
                LOG.warning("Static rule for '%s' already found.  Discarding "
                            "older entry" % (target,))

            # CA-82901 - Accept old-style ppns (pciXpY), but translate to
            # new-style ones (pXpY)
            if method == "ppn" and value.startswith("pci"):
                value = "p" + value[3:]
                LOG.warning("Detected use of old-style ppn reference for %s "
                            "on line %d - Translating to %s"
                            % (target, num, value) )

            self.formulae[target] = (method, value)

        return True

    def generate(self, state):
        """
        Make rules from the formulae based on global state.
        """

        # CA-75599 - check that state has no shared ppns.
        #  See net.biodevname.has_ppn_quirks() for full reason
        ppns = [ x.ppn for x in state if x.ppn is not None ]
        ppn_quirks = ( len(ppns) != len(set(ppns)) )

        if ppn_quirks:
            LOG.warning("Discovered physical policy naming quirks in provided "
                        "state.  Disabling 'method=ppn' generation")

        for target, (method, value) in self.formulae.iteritems():

            if method == "mac":

                for nic in state:
                    if nic.mac == value:
                        try:
                            rule = MACPCI(nic.mac, nic.pci, tname=target)
                        except Exception, e:
                            LOG.warning("Error creating rule: %s" % (e,))
                            continue
                        self.rules.append(rule)
                        break
                else:
                    LOG.warning("No NIC found with a MAC address of '%s' for "
                                "the %s static rule" % (value, target))
                continue

            elif method == "ppn":

                if ppn_quirks:
                    LOG.info("Not considering formula for '%s' due to ppn "
                             "quirks" % (target,))
                    continue

                for nic in state:
                    if nic.ppn == value:
                        try:
                            rule = MACPCI(nic.mac, nic.pci, tname=target)
                        except Exception, e:
                            LOG.warning("Error creating rule: %s" % (e,))
                            continue
                        self.rules.append(rule)
                        break
                else:
                    LOG.warning("No NIC found with a ppn of '%s' for the "
                                "%s static rule" % (value, target))
                continue

            elif method == "pci":

                for nic in state:
                    if nic.pci == value:
                        try:
                            rule = MACPCI(nic.mac, nic.pci, tname=target)
                        except Exception, e:
                            LOG.warning("Error creating rule: %s" % (e,))
                            continue
                        self.rules.append(rule)
                        break
                else:
                    LOG.warning("No NIC found with a PCI ID of '%s' for the "
                                "%s static rule" % (value, target))
                continue

            elif method == "label":

                for nic in state:
                    if nic.label == value:
                        try:
                            rule = MACPCI(nic.mac, nic.pci, tname=target)
                        except Exception, e:
                            LOG.warning("Error creating rule: %s" % (e,))
                            continue
                        self.rules.append(rule)
                        break
                else:
                    LOG.warning("No NIC found with an SMBios Label of '%s' for "
                                "the %s static rule" % (value, target))
                continue

            else:
                LOG.critical("Unknown static rule method %s" % method)

    def write(self, header = True):

        res = ""

        if header:
            res += SAVE_HEADER

        keys = list(set(( x for x in self.formulae.keys()
                          if x.startswith("eth") )))
        keys.sort(key=lambda x: int(x[3:]))

        for target in keys:
            method, value = self.formulae[target]

            if method not in StaticRules.methods:
                LOG.warning("Method %s not recognised.  Ignoring" % (method,))
                continue

            # If we have a validator, test the valididy
            if method in StaticRules.validators:
                if StaticRules.validators[method].match(value) is None:
                    LOG.warning("Invalid %s value '%s'. Ignoring"
                                % (method, value))
                    continue

            res += "%s:%s=\"%s\"\n" % (target, method, value)

        return res

    def save(self, header = True):

        fd = None

        try:
            try:
                # If we were given a path, try opening and writing to it
                if self.path:
                    fd = open(self.path, "w")
                    fd.write(self.write(header))

                # else if we were given a file descriptor, just read it
                elif self.fd:
                    self.fd.write(self.write(header))

                # else there is nothing we can do
                else:
                    LOG.error("No source of data to parse")
                    return False

            except IOError, e:
                LOG.error("IOError while reading file: %s" % (e,))
                return False
        finally:
            # Ensure we alway close the file descriptor we opened
            if fd:
                fd.close()

        return True
