# -*- coding: utf-8 -*-
"""Целостность словаря переводов.

Класс дефекта, из-за которого файл появился: **`translations.py` расширяется
блоками `TRANS[...].update({...})`, и повторное присваивание ключа молча
побеждает предыдущее.** Экран предупреждений АЗС завёл `'Фаол': 'Активные'`
для счётчика предупреждений — и бейдж состояния ОДНОГО пользователя на
`/admin/users` стал «Активные». Ни ошибки, ни предупреждения: словарь —
обычный `dict`, последнее присваивание выигрывает.

Проверка идёт по ИСХОДНИКУ, а не по собранному словарю: в собранном видно
только победителя, и дефект в нём не отличим от корректного объявления.
"""

import ast
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(REPO_ROOT, 'translations.py')


def _assignments():
    """Вернуть [(язык, ключ, значение, строка)] по всем объявлениям файла.

    Разбирается и литерал `TRANS = {...}`, и все блоки
    `TRANS['uz'].update({...})`.
    """
    with open(PATH, encoding='utf-8') as handle:
        tree = ast.parse(handle.read(), PATH)
    out = []

    def harvest(lang, dict_node):
        for key_node, value_node in zip(dict_node.keys, dict_node.values):
            if not isinstance(key_node, ast.Constant):
                continue
            if not isinstance(value_node, ast.Constant):
                continue
            out.append((lang, key_node.value, value_node.value, key_node.lineno))

    for node in ast.walk(tree):
        # TRANS = {'uz': {...}, 'ru': {...}}
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == 'TRANS'
                and isinstance(node.value, ast.Dict)):
            for lang_node, body in zip(node.value.keys, node.value.values):
                if isinstance(lang_node, ast.Constant) and isinstance(body, ast.Dict):
                    harvest(lang_node.value, body)
        # TRANS['uz'].update({...})
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'update'
                and isinstance(node.func.value, ast.Subscript)
                and isinstance(node.func.value.value, ast.Name)
                and node.func.value.value.id == 'TRANS'
                and node.args and isinstance(node.args[0], ast.Dict)):
            index = node.func.value.slice
            if isinstance(index, ast.Constant):
                harvest(index.value, node.args[0])
    return out


class DictionaryIntegrity(unittest.TestCase):

    def test_no_key_is_redefined_with_a_different_value(self):
        """Один ключ — один перевод. Разные смыслы требуют разных ключей.

        Отрицательный контроль встроен в саму форму проверки: она читает
        исходник, поэтому видит ОБА присваивания. Проверка по собранному
        словарю показала бы одинаковый результат при верном и неверном коде
        и проверкой бы не являлась.
        """
        seen = {}
        clashes = []
        for lang, key, value, line in _assignments():
            prev = seen.get((lang, key))
            if prev is not None and prev[0] != value:
                clashes.append(
                    '%s[%r]: строка %d даёт %r, строка %d даёт %r'
                    % (lang, key, prev[1], prev[0], line, value))
            seen[(lang, key)] = (value, line)
        self.assertEqual(clashes, [], 'ключ переопределён другим значением:\n'
                                      + '\n'.join(clashes))

    def test_both_languages_carry_the_same_key_set(self):
        """Ключ, объявленный на одной стороне, обязан быть и на другой.

        Иначе `t()` вернёт сам ключ ровно для одного из двух языков — а это
        тот же молчаливый отказ, что был найден в модуле Виалон.
        """
        from translations import TRANS
        only_uz = sorted(set(TRANS['uz']) - set(TRANS['ru']))
        only_ru = sorted(set(TRANS['ru']) - set(TRANS['uz']))
        self.assertEqual(only_uz, [], 'ключи без русской стороны: %s' % only_uz)
        self.assertEqual(only_ru, [], 'ключи без узбекской стороны: %s' % only_ru)

    def test_the_parser_actually_finds_the_dictionary(self):
        """Разбор обязан что-то находить.

        Без этого предыдущие два теста прошли бы на пустом списке и означали
        бы ровно ничего.
        """
        rows = _assignments()
        self.assertGreater(len(rows), 800, 'разбор нашёл подозрительно мало')
        langs = {lang for lang, _k, _v, _l in rows}
        self.assertEqual(langs, {'uz', 'ru'})


if __name__ == '__main__':
    unittest.main()
