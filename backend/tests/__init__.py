"""测试包。

显式声明为包（含 ``__init__.py``）有两个作用：

1. 让 ``from tests.conftest import requires_db`` 这类**跨文件共享夹具**可用
   （否则只能靠 ``conftest.py`` 的自动发现，无法 import）；
2. 让 pytest 的 rootdir（``backend/``）被加入 ``sys.path``，从而 ``import app`` 可用 ——
   与 ``--import-mode=prepend``（默认）配合，无需安装成 site-package 即可测试。
"""
