from .s_memory import SimMemory
from .s_exception import SimMergeError

import logging
l = logging.getLogger("simuvex.s_file")

# TODO: symbolic file positions

class Flags:
	O_RDONLY = 0
	O_WRTONLY = 1
	O_RDWR = 2
	O_APPEND = 4096
	O_ASYNC = 64
	O_CLOEXEC = 512
	# TODO mode for this flag
	O_CREAT = 256
	O_DIRECT = 262144
	O_DIRECTORY = 2097152
	O_EXCL = 2048
	O_LARGEFILE = 1048576
	O_NOATIME = 16777216
	O_NOCTTY = 1024
	O_NOFOLLOW = 4194304
	O_NONBLOCK = 8192
	O_NODELAY = 8192
	O_SYNC = 67174400
	O_TRUNC = 1024


class SimFile:
	# Creates a SimFile
	def __init__(self, fd, name, mode, content=None):
		self.fd = fd
		self.pos = 0
		self.name = name
		self.mode = mode
		self.content = SimMemory(memory_id="file_%d_%s_%s" % (fd, name, mode)) if content is None else content

		# TODO: handle symbolic names, special cases for stdin/out/err
		# TODO: read content for existing files

	# Reads some data from the current position of the file.
	def read(self, length, pos=None):
		# TODO: error handling
		# TODO: symbolic length?

		if pos is None:
			data = self.content.load(self.pos, length)
			self.pos += length
		else:
			data = self.content.load(pos, length)
			pos += length
		return data

	# Writes some data to the current position of the file.
	def write(self, content, length, pos=None):
		# TODO: error handling
		# TODO: symbolic length?

		if pos is None:
			self.content.store(self.pos, content)
			self.pos += length
		else:
			self.content.store(pos, content)
			pos += length
		return length

	# Seeks to a position in the file.
	def seek(self, where):
		self.pos = where

	# Copies the SimFile object.
	def copy(self):
		c = SimFile(self.fd, self.name, self.mode, self.content.copy())
		c.pos = self.pos
		return c

	# Merges the SimFile object with another one.
	def merge(self, other, merge_flag, flag_us_value):
		if self.fd != other.fd:
			raise SimMergeError("files have different FDs")

		if self.pos != other.pos:
			raise SimMergeError("merging file positions is not yet supported (TODO)")

		if self.name != other.name:
			raise SimMergeError("merging file names is not yet supported (TODO)")

		if self.mode != other.mode:
			raise SimMergeError("merging modes is not yet supported (TODO)")

		return self.content.merge(other, merge_flag, flag_us_value)