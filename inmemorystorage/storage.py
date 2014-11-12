from io import StringIO, BytesIO
import os
try:
    from urlparse import urljoin
except ImportError:
    from urllib.parse import urljoin


from django.conf import settings
from django.core.files.storage import Storage
from django.core.files.base import File
try:
    from django.utils.encoding import filepath_to_uri, force_bytes
except ImportError:
    # django.utils.encoding.force_bytes doesn't exist in < 1.5
    from django.utils.encoding import smart_str as force_bytes
import six

from django.core.files.storage import Storage
from django.core.files.base import ContentFile
from django.utils.encoding import filepath_to_uri
from django.utils.six.moves.urllib.parse import urljoin
from django.utils import timezone


class PathDoesNotExist(Exception):
    pass


class InMemoryNode(object):
    """
    Base class for files and directories.
    """
    parent = None

    def add_child(self, name, child):
        child.parent = self
        self.children[name] = child


class InMemoryFile(InMemoryNode, File):
    """
    Stores contents of file and stores reference to parent. File interface is identical
    to ContentFile, except that self.size works even after data has been written to it
    """
    def __init__(self, content='', parent=None, name=None):
        #init InMemoryNode
        self.parent = parent
        self.created_at = timezone.now()
        self.last_modified = timezone.now()
        self.last_accessed = timezone.now()

        #init File
        if six.PY3:
            stream_class = StringIO if isinstance(content, six.text_type) else BytesIO
        else:
            stream_class = six.BytesIO
            content = force_bytes(content)
        File.__init__(self, stream_class(content), name=name)

    def __str__(self):
        return '<InMemoryFile: %s>' % self.name

    def __bool__(self):
        return True

    def __nonzero__(self):      # Python 2 compatibility
        return type(self).__bool__(self)

    @property
    def _size(self):
        pos = self.file.tell()
        self.file.seek(0, os.SEEK_END)
        size = self.file.tell()
        self.file.seek(pos)
        return size

    def open(self, mode=None):
        self.seek(0)

    def close(self):
        pass


class InMemoryDir(InMemoryNode):
    """
    Stores dictionary of child directories/files and reference to parent.
    """
    def __init__(self, dirs=None, files=None, parent=None):
        self.children = {}
        self.parent = parent

    def resolve(self, path, create=False, byte=False):
        path_bits = path.strip('/').split('/', 1)
        current = path_bits[0]
        rest = path_bits[1] if len(path_bits) > 1 else None
        if not rest:
            if current == '':
                return self
            if current in self.children.keys():
                return self.children[current]
            if not create:
                raise PathDoesNotExist()
            node = InMemoryFile(name=current, content=b'' if byte else u'')
            self.add_child(current, node)
            return node
        if current in self.children.keys():
            return self.children[current].resolve(rest, create=create, byte=byte)
        if not create:
            raise PathDoesNotExist()
        node = InMemoryDir()
        self.add_child(current, node)
        return self.children[current].resolve(rest, create=create, byte=byte)

    def ls(self, path=''):
        return list(self.resolve(path).children.keys())

    def listdir(self, dir):
        nodes = tuple(six.iteritems(self.resolve(dir).children))
        dirs = [k for (k, v) in nodes if isinstance(v, InMemoryDir)]
        files = [k for (k, v) in nodes if isinstance(v, InMemoryFile)]
        return [dirs, files]

    def delete(self, path):
        node = self.resolve(path)
        for name, child in six.iteritems(node.parent.children):
            if child is node:
                del node.parent.children[name]
                break

    def exists(self, name):
        try:
            self.resolve(name)
        except PathDoesNotExist:
            return False
        else:
            return True

    def size(self, name):
        return self.resolve(name).size

    def open(self, path, mode="r"):
        create = "w" in mode
        byte = "b" in mode
        f = self.resolve(path, create=create, byte=byte)
        f.open(mode)
        f.last_accessed = timezone.now()
        return f

    def save(self, path, content):
        mode = 'wb' if isinstance(content, six.binary_type) else 'w'
        with self.open(path, mode) as f:
            f.write(content)
        f.last_modified = timezone.now()
        return path


class InMemoryStorage(Storage):
    """
    Django storage class for in-memory filesystem.
    """
    def __init__(self, filesystem=None, base_url=None):
        self.filesystem = filesystem or InMemoryDir()

        if base_url is None:
            base_url = settings.MEDIA_URL
        self.base_url = base_url

    def listdir(self, dir):
        return self.filesystem.listdir(dir)

    def delete(self, path):
        return self.filesystem.delete(path)

    def exists(self, name):
        return self.filesystem.exists(name)

    def size(self, name):
        return self.filesystem.size(name)

    def _open(self, name, mode="r"):
        return self.filesystem.open(name, mode)

    def _save(self, name, content):
        return self.filesystem.save(name, content.read())

    def url(self, name):
        if self.base_url is None:
            raise ValueError("This file is not accessible via a URL.")
        return urljoin(self.base_url, filepath_to_uri(name))

    def modified_time(self, name):
        file = self.filesystem.resolve(name)
        return file.last_modified

    def accessed_time(self, name):
        file = self.filesystem.resolve(name)
        return file.last_accessed

    def created_time(self, name):
        file = self.filesystem.resolve(name)
        return file.created_at
