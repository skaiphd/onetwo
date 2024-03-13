# Copyright 2024 DeepMind Technologies Limited.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import collections
from collections.abc import Sequence
import copy
import dataclasses
import os
import pprint

from absl.testing import absltest
from absl.testing import parameterized
import numpy as np
from onetwo.core import caching
from onetwo.core import constants
from onetwo.core import executing
from onetwo.core import updating


class KeyForTest:
  def __init__(self, value: str):
    self.value = value

  def __hash__(self) -> int:
    return hash(self.value + ' ' + self.value)


class TestCache(caching.SimpleCache[str]):
  """Implementation of abstract SimpleCache for tests.

  Uses a tuple (key, sampling_key) as a cache key.
  """

  def __init__(self, append_when_caching=False):
    self.contents = {}
    self.append_when_caching = append_when_caching

  def cache_value(
      self,
      key: str,
      sampling_key: str | None,
      value: str,
  ) -> None:
    if self.append_when_caching:
      if (key, sampling_key) not in self.contents:
        self.contents[(key, sampling_key)] = []
      self.contents[(key, sampling_key)].append(value)
    else:
      self.contents[(key, sampling_key)] = value

  async def get_cached_value(
      self,
      key: str,
      sampling_key: str | None,
  ) -> str | None:
    res = self.contents.get((key, sampling_key), None)
    if res is None:
      return None
    if self.append_when_caching:
      res = res[0]
    assert isinstance(res, str)  # pytype hint.
    return res


@dataclasses.dataclass
class TestKeyMaker(caching.CacheKeyMaker):
  """Simplified version of CacheKeyMaker for tests.

  A version of CacheKeyMaker with simpler implementation of create_key.
  """

  def create_key(self, obj_with_cache, args, kwargs) -> str:
    del obj_with_cache
    if len(args) == 2:
      return args[0] + args[1]
    else:
      return args[0] + kwargs['b']


class ClassWithCachedMethods(caching.CacheEnabled[str]):
  """An implementation of abstract CacheEnabled that uses TestCache."""

  def __init__(self, append_when_caching=False, disable_caching=False):
    self._cache = TestCache(append_when_caching=append_when_caching)
    self._disable_caching = disable_caching

  @property
  def cache_handler(self) -> TestCache:
    """Returns the cache object for this class."""
    return self._cache

  @property
  def disable_caching(self) -> bool:
    """Returns the cache object for this class."""
    return self._disable_caching

  @caching.cache_method(
      name='method_decorated_with_default_cache_key_maker',
      is_sampled=True
  )
  def method_decorated_with_default_cache_key_maker(
      self, a: str, b: str
  ) -> str:
    return a + b

  @caching.cache_method(
      name='method_decorated_with_cache_extra_replies',
      is_sampled=True,
      cache_extra_replies=True,
  )
  def method_decorated_with_cache_extra_replies(
      self, a: str, b: str
  ) -> Sequence[str]:
    return [a + b, 'extra1 ' + a + b, 'extra2 ' + a + b]

  @caching.cache_method(
      name='method_does_not_return_sequence',
      is_sampled=True,
      cache_extra_replies=True,
  )
  def method_does_not_return_sequence(
      self, a: str, b: str
  ) -> str:
    return a + b

  @caching.cache_method(
      name='method_with_default_args',
      is_sampled=True
  )
  def method_with_default_args(
      self, a: str, b: str, c: str = ''
  ) -> str:
    del c
    return a + b

  @caching.cache_method(
      name='method_decorated_with_test_cache_key_maker',
      is_sampled=True,
      cache_key_maker=TestKeyMaker
  )
  def method_decorated_with_test_cache_key_maker(self, a: str, b: str) -> str:
    return a + b

  @caching.cache_method(
      name='method_decorated_with_hash_and_kwargs',
      is_sampled=True,
      cache_key_maker=caching.CacheKeyMaker(hashed=['a']),
  )
  def method_decorated_with_hash_and_kwargs(self, a: str, **extra) -> str:
    return a + extra['b']

  @caching.cache_method(
      name='method_with_var_positional',
      is_sampled=True,
  )
  def method_with_var_positional(self, a: str, b: str, *args) -> str:
    del args
    return a + b

  @caching.cache_method(
      is_sampled=False,
      cache_key_maker=lambda: caching.CacheKeyMaker(dropped=['a']),
  )
  def method_with_explicit_arg(self, a: str, b: str) -> str:
    return a + b

  @caching.cache_method(
      is_sampled=False,
      cache_key_maker=lambda: caching.CacheKeyMaker(dropped=['a']),
  )
  def method_with_implicit_arg(self, **kwargs) -> str:
    return kwargs['a'] + kwargs['b']

  @executing.make_executable
  @caching.cache_method(is_sampled=False)
  def method_which_returns_executable(self, text: str) -> str:
    @executing.make_executable
    def stream():
      for i in range(1, len(text)):
        yield text[:i]
      yield text + ' done'

    return stream()


class SomeClass:

  @caching.cache_method(
      name='method_not_of_cacheenabled',
      is_sampled=True,
  )
  def method_not_of_cacheenabled(self, a: str, b: str) -> str:
    del a, b
    return ''


class CacheDecorationTest(parameterized.TestCase):
  """Tests cache_method with CacheKeyMaker, SimpleCache, and CacheEnabled."""

  @parameterized.named_parameters(
      (
          'no_key_maker',
          'method_decorated_with_default_cache_key_maker',
          (
              (
                  f'{{"{constants.CACHING_FUNCTION_NAME_KEY}": '
                  '"method_decorated_with_default_cache_key_maker", '
                  '"a": "test", "b": " done"}',
                  '',
              )
          ),
      ),
      (
          'default_args_with_key_maker',
          'method_with_default_args',
          (
              (
                  f'{{"{constants.CACHING_FUNCTION_NAME_KEY}": '
                  '"method_with_default_args", '
                  '"a": "test", "b": " done", "c": ""}',
                  '',
              )
          ),
      ),
      (
          'use_TestKeyMaker',
          'method_decorated_with_test_cache_key_maker',
          ('test done', ''),
      ),
      (
          'key_maker_with_hash_and_kwargs',
          'method_decorated_with_hash_and_kwargs',
          (
              (
                  f'{{"{constants.CACHING_FUNCTION_NAME_KEY}": '
                  '"method_decorated_with_hash_and_kwargs", "a":'
                  ' "90a3ed9e32b2aaf4c61c410eb925426119e1a9dc53d4286ade99a809",'
                  ' "b": " done"}'
              ),
              '',
          ),
      ),
  )
  def test_cache_key(self, method, expected_keys):
    backend = ClassWithCachedMethods()
    result = asyncio.run(
        getattr(ClassWithCachedMethods, method)(backend, 'test', b=' done')
    )
    handler: TestCache = getattr(backend, 'cache_handler')
    list_keys = list(handler.contents.keys())
    # Hint: we only have one key in the cache.
    assert len(list_keys) == 1
    keys = list_keys[0]
    self.assertEqual(keys, expected_keys)
    self.assertEqual(result, 'test done')

  def test_method_with_var_positional(self):
    backend = ClassWithCachedMethods()
    result = asyncio.run(backend.method_with_var_positional('a', 'b', 'c'))
    handler: TestCache = getattr(backend, 'cache_handler')
    keys = list(handler.contents.keys())
    keys = keys[0]
    self.assertEqual(
        keys,
        (
            (
                f'{{"{constants.CACHING_FUNCTION_NAME_KEY}":'
                ' "method_with_var_positional", "a": "a", "args": ["c"], "b":'
                ' "b"}'
            ),
            '',
        ),
        repr(keys),
    )
    self.assertEqual(result, 'ab')

  @parameterized.named_parameters(
      (
          'disable_cache_true',
          True,
          [],
      ),
      (
          'disable_cache_false',
          False,
          [
              'test done',
              'extra1 test done',
              'extra2 test done'
          ],
      ),
  )
  def test_cache_extra_replies(self, disable_cache, expected_cached_values):
    backend = ClassWithCachedMethods(
        append_when_caching=True, disable_caching=disable_cache
    )
    method = 'method_decorated_with_cache_extra_replies'
    result = asyncio.run(
        getattr(ClassWithCachedMethods, method)(backend, 'test', b=' done')
    )
    self.assertEqual(result, 'test done')
    # Check that two extra values were cached.
    handler: TestCache = getattr(backend, 'cache_handler')
    values = list(handler.contents.values())
    if not disable_cache:
      # We have two keys: first reply with sampling_key='', second with None.
      assert len(values) == 2, pprint.pformat(handler.contents)
      cached_values = values[0] + values[1]
      self.assertCountEqual(
          cached_values,
          expected_cached_values,
          pprint.pformat(cached_values)
      )
    else:
      # Make sure nothing is cached.
      assert not values, pprint.pformat(handler.contents)

  def test_does_not_return_seq_cache_extra_replies_raises_exception(self):
    backend = ClassWithCachedMethods()
    with self.assertRaisesRegex(
        ValueError,
        'Method that is decorated with cache_method(cache_extra_replies=True)*'
    ):
      _ = asyncio.run(
          backend.method_does_not_return_sequence('test', b=' done')
      )

  def test_decorate_non_method_raises_exception(self):
    with self.assertRaisesRegex(
        ValueError,
        'Decorator @cache_method should be applied to a method*'
    ):
      @caching.cache_method(
          name='_',
          is_sampled=True,
      )
      def _(a: str, b: str) -> str:
        del a, b
        return ''

  def test_decorate_method_not_of_cacheenabled_raises_exception(self):
    backend = SomeClass()
    with self.assertRaisesRegex(ValueError, '.*inherit from CacheEnabled'):
      _ = asyncio.run(backend.method_not_of_cacheenabled('test', b=' done'))

  def test_drop_keys(self):
    backend = ClassWithCachedMethods()
    results = []
    results.append(
        asyncio.run(backend.method_with_explicit_arg(a='test', b=' done'))
    )
    results.append(
        asyncio.run(backend.method_with_explicit_arg(a='test2', b=' done'))
    )
    results.append(
        asyncio.run(backend.method_with_implicit_arg(a='test', b=' done'))
    )
    results.append(
        asyncio.run(backend.method_with_implicit_arg(a='test2', b=' done'))
    )
    handler: TestCache = getattr(backend, 'cache_handler')
    values = list(handler.contents.values())
    with self.subTest('ignores_a_as_cache_key'):
      self.assertListEqual(results, ['test done'] * 4)
    with self.subTest('stores_two_values'):
      self.assertListEqual(values, ['test done'] * 2)

  def test_cache_after_execution(self):
    backend = ClassWithCachedMethods()
    handler: TestCache = getattr(backend, 'cache_handler')
    contents = handler.contents

    # Full execution which should be handled by the make_executable decorator,
    # hence the cache_method decorator does not see an Executable as the return
    # value.
    result = executing.run(backend.method_which_returns_executable('test'))
    with self.subTest('cached_the_value'):
      self.assertEqual(result, 'test done')
      self.assertListEqual(list(contents.values()), ['test done'])

    # Iterative execution
    stream = []
    @executing.make_executable
    async def wrapper():
      nonlocal stream
      # Fill in the cache (by full execution).
      _ = await backend.method_which_returns_executable('it')
      # This will read from the cache and get directly a string.
      e = await backend.method_which_returns_executable('it').pre_execute()
      stream.append(e)
      # This will not read from the cache and thus return an Executable.
      e = await backend.method_which_returns_executable('it2').pre_execute()
      result = None
      async for result in e:
        stream.append(result)
      return result

    _ = executing.run(wrapper())
    with self.subTest('cached_the_value'):
      self.assertEqual(
          stream,
          [
              'it done',
              updating.Update(payload='i'),
              updating.Update(payload='it'),
              updating.Update(payload='it2 done'),
          ],
      )
      self.assertListEqual(
          list(contents.values()), ['test done', 'it done', 'it2 done']
      )


class ClassCachedWithSimpleFunctionCache(caching.CacheEnabled[str]):

  def __init__(self):
    self._cache = caching.SimpleFunctionCache(cache_filename='test')

  @property
  def cache_handler(self) -> caching.SimpleFunctionCache:
    """See parent class."""
    return self._cache

  @property
  def disable_caching(self) -> bool:
    """See parent class."""
    return False

  @caching.cache_method(is_sampled=False)
  def method_that_may_raise_errors(
      self,
      a: str,
      raise_error: bool = False,
  ) -> str:
    if raise_error:
      raise ValueError('We raise error.')
    return f'{a} result'


class SimpleFunctionCacheTest(parameterized.TestCase):
  """Tests SimpleFunctionCache implementation of abstract SimpleCache."""

  def test_cache_file_path_exists_raises_exception(self):
    cache_dir = self.create_tempdir()
    cache_filename = 'my_cache'
    # This file has the same path as the one that `write_to_directory` uses.
    tmp_filename = os.path.join(
        cache_dir.full_path,
        caching.add_json_extension(cache_filename)
    )
    _ = self.create_tempfile(tmp_filename)
    function_cache = caching.SimpleFunctionCache(
        cache_filename=cache_filename,
        cached_value_decoder=lambda x: x,
    )
    with self.assertRaisesRegex(FileExistsError, '.*already exists.'):
      function_cache.write_to_directory(cache_dir.full_path)

  def test_cache_and_get_cached_value(self):

    function_cache = caching.SimpleFunctionCache(
        cache_filename='my_cache',
        cached_value_decoder=lambda x: x,
    )
    sampling_key_none = None
    # New cache key.
    function_cache.cache_value('key1', sampling_key_none, 'value_1')
    # New cache key.
    function_cache.cache_value('key2', sampling_key_none, 'value_2')
    with self.subTest('cache_new_cache_key'):
      self.assertEqual(
          {
              'add_new': 2,
          },
          function_cache._counters,
      )
      self.assertEqual(
          collections.defaultdict(int, {}),
          function_cache._num_used_values_by_key,
      )
    # New cache key, sample_id updates.
    function_cache.cache_value('key3', 'sampling_key_1', 'value_3')
    with self.subTest('cache_new_cache_key_with_sample_update'):
      self.assertEqual(
          {
              'add_new': 3,
          },
          function_cache._counters,
      )
      self.assertEqual(
          collections.defaultdict(int, {caching._get_hash('key3'): 1}),
          function_cache._num_used_values_by_key,
      )
    # Matched cache key, no sampling_key, append value.
    function_cache.cache_value('key1', sampling_key_none, 'value_4')
    with self.subTest('cache_matched_cache_key'):
      self.assertEqual(
          {
              'add_new': 3,
              'add_new_sample': 1,
          },
          function_cache._counters,
      )
      self.assertEqual(
          collections.defaultdict(int, {caching._get_hash('key3'): 1}),
          function_cache._num_used_values_by_key,
      )
    # Matched cache key, new sampling_key, append value, sample_id updates.
    function_cache.cache_value('key1', 'sampling_key_1', 'value_5')
    with self.subTest('cache_matched_cache_key_with_sample_update'):
      self.assertEqual(
          {
              'add_new': 3,
              'add_new_sample': 2,
          },
          function_cache._counters,
      )
      self.assertEqual(
          collections.defaultdict(int, {
              caching._get_hash('key3'): 1,
              caching._get_hash('key1'): 1,
          }),
          function_cache._num_used_values_by_key,
      )
      # By now we have 3 values for this key.
      self.assertEqual(
          ['value_1', 'value_4', 'value_5'],
          function_cache._values_by_key[caching._get_hash('key1')],
      )
    with self.subTest(
        'get_cached_existing_sampling_key_returns_not_the_last_element'
    ):
      # For the existing sampling_key we get the very first value.
      self.assertEqual(
          asyncio.run(
              function_cache.get_cached_value('key1', 'sampling_key_1')
          ),
          'value_1',
      )
      # And sample ids don't change.
      self.assertEqual(
          collections.defaultdict(int, {
              caching._get_hash('key3'): 1,
              caching._get_hash('key1'): 1,
          }),
          function_cache._num_used_values_by_key,
      )
    with self.subTest(
        'get_cached_new_sampling_key_maps_new_sample'
    ):
      # For the new sampling_key we get the second value mapped.
      self.assertEqual(
          asyncio.run(
              function_cache.get_cached_value('key1', 'sampling_key_2')
          ),
          'value_4',
      )
      # And sampling ids change.
      self.assertEqual(
          collections.defaultdict(int, {
              caching._get_hash('key3'): 1,
              caching._get_hash('key1'): 2,
          }),
          function_cache._num_used_values_by_key,
      )
    # Matched cache key, matched sampling_key, matched value. Redundant.
    function_cache.cache_value('key1', 'sampling_key_1', 'value_1')
    with self.subTest(
        'cache_matched_cache_key_matched_sampling_key_same_value'
    ):
      self.assertEqual(
          {
              'add_new': 3,
              'add_new_sample': 2,
              'add_redundant': 1,
              'get_hit': 2,
          },
          function_cache._counters,
      )
      self.assertEqual(
          collections.defaultdict(int, {
              caching._get_hash('key3'): 1,
              caching._get_hash('key1'): 2,
          }),
          function_cache._num_used_values_by_key,
      )
    # Matched cache key, matched sampling_key, new value. Overwrite.
    function_cache.cache_value('key1', 'sampling_key_1', 'value_2')
    with self.subTest(
        'cache_matched_cache_key_matched_sampling_key_new_value'
    ):
      self.assertEqual(
          {
              'add_new': 3,
              'add_new_sample': 2,
              'add_redundant': 1,
              'add_overwrote': 1,
              'get_hit': 2,
          },
          function_cache._counters,
      )
      self.assertEqual(
          collections.defaultdict(int, {
              caching._get_hash('key3'): 1,
              caching._get_hash('key1'): 2,
          }),
          function_cache._num_used_values_by_key,
      )
    # Deterministic function.
    function_cache.cache_value('key_det', sampling_key_none, 'value_1')
    with self.subTest(
        'get_cached_deterministic_one_value_no_sampling_key'
    ):
      self.assertEqual(
          asyncio.run(
              function_cache.get_cached_value('key_det', sampling_key_none)
          ),
          'value_1',
      )

  def test_decorated_methods_raise_errors(self):
    backend = ClassCachedWithSimpleFunctionCache()
    with self.subTest('cache_decorator_properly_handles_exceptions'):
      with self.assertRaisesRegex(ValueError, 'We raise error*'):
        _ = asyncio.run(
            backend.method_that_may_raise_errors(a='some', raise_error=True)
        )
    # pytype hint.
    handler: caching.SimpleFunctionCache = getattr(backend, 'cache_handler')
    with self.subTest('calls_in_progress_cleared_after_exception'):
      self.assertEqual(handler.calls_in_progress, set())

  def test_write_to_and_load_from_disk(self):
    cache_filename = 'my_cache'
    function_cache = caching.SimpleFunctionCache(
        cache_filename=cache_filename,
        cached_value_decoder=lambda x: x,
    )
    cache_dir = self.create_tempdir()
    cache_file_path = os.path.join(
        cache_dir.full_path,
        caching.add_json_extension(cache_filename)
    )
    sampling_key_none = None
    function_cache.cache_value('key1', sampling_key_none, 'value_1')
    function_cache.cache_value('key1', sampling_key_none, 'value_2')
    function_cache.cache_value('key1', sampling_key_none, 'value_3')
    function_cache.cache_value('key1', 'sampling_key_1', 'value_4')
    function_cache.cache_value('key1', 'sampling_key_2', 'value_5')
    _ = asyncio.run(function_cache.get_cached_value('key1', 'sampling_key_3'))
    function_cache.cache_value('key2', sampling_key_none, 'value_6')
    function_cache.cache_value('key2', 'sampling_key_4', 'value_7')
    function_cache.cache_value('key3', 'sampling_key_5', 'value_8')
    function_cache.write_to_directory(cache_dir.full_path)
    with self.subTest('cache_file_exists'):
      self.assertTrue(os.path.exists(cache_file_path))
    # Cache with restored sample id mappings.
    cache_1 = caching.SimpleFunctionCache.create_from_file(
        cache_file_path, restore_mapping=True)
    with self.subTest(
        'cache_restored_properly_with_sample_mapping'
    ):
      self.assertEqual(
          asyncio.run(cache_1.get_cached_value('key1', 'sampling_key_2')),
          'value_2',
      )
      self.assertEqual(
          asyncio.run(cache_1.get_cached_value('key1', 'sampling_key_1')),
          'value_1',
      )
    # Cache with fresh sample id mappings.
    cache_2 = caching.SimpleFunctionCache.create_from_file(cache_file_path)
    with self.subTest(
        'cache_restored_properly_with_fresh_sample_mapping'
    ):
      self.assertEqual(
          asyncio.run(cache_2.get_cached_value('key1', 'sampling_key_2')),
          'value_1',
      )
      self.assertEqual(
          asyncio.run(cache_2.get_cached_value('key1', 'sampling_key_1')),
          'value_2',
      )

  @parameterized.named_parameters(
      ('str', 'key1', 'key1', 'key2'),
      ('list', [1, 2, 3], [1, 2, 3], [3, 2, 1]),
      ('set', {'a', 'b'}, {'b', 'a'}, {'a', 'c'}),
      ('dict', {'a': 1, 'b': 2}, {'b': 2, 'a': 1}, {'a': 1, 'b': 3}),
      ('bytes', b'a', b'a', b'b'),
      (
          'np.array',
          np.array([1, 2, 3]),
          np.array([1, 2, 3]),
          np.array([3, 2, 1]),
      ),
      ('hashable', KeyForTest('a'), KeyForTest('a'), KeyForTest('b')),
  )
  def test_get_hash(self, key, similar_key, other_key):
    # We create a simple copy of the key.
    c = copy.deepcopy(key)
    key_hash = caching._get_hash(key)
    copy_hash = caching._get_hash(c)
    similar_hash = caching._get_hash(similar_key)
    other_hash = caching._get_hash(other_key)
    # We check we obtain the same hash (this is not a very strong test
    # as ideally one would compare hashes on different machines etc... but
    # this is a first attempt at checking that some reasonable hash is created).
    self.assertEqual(copy_hash, key_hash)
    self.assertEqual(similar_hash, key_hash)
    # We also check that the hashes are different for different keys (this
    # would catch mistakes such as accidentally mapping all keys to the same
    # hash).
    self.assertNotEqual(other_hash, key_hash)


if __name__ == '__main__':
  absltest.main()