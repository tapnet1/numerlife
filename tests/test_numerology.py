import datetime as dt
import unittest

import numerology as n


class ReduceTest(unittest.TestCase):
    def test_reduce_and_master_numbers(self):
        self.assertEqual(n.reduce(1990), 1)       # 19 -> 10 -> 1
        self.assertEqual(n.reduce(29), 11)        # master kept
        self.assertEqual(n.reduce(29, keep_master=False), 2)
        self.assertEqual(n.reduce(1984), 22)      # 22 kept
        self.assertEqual(n.reduce(7), 7)

    def test_letter_values(self):
        self.assertEqual([n.letter_value(c) for c in "AIJRSZ"], [1, 9, 1, 9, 1, 8])


class CoreNumbersTest(unittest.TestCase):
    def test_worked_example(self):
        # J1 O6 H8 N5 = 20; S1 M4 I9 T2 H8 = 24; total 44 -> 8
        # vowels O + I = 15 -> 6; consonants 1+8+5+1+4+2+8 = 29 -> 11
        # life path: 7 + (15 -> 6) + (1990 -> 1) = 14 -> 5
        numbers = n.core_numbers("John Smith", dt.date(1990, 7, 15))
        self.assertEqual(numbers.as_dict(), {
            "life_path": 5, "expression": 8, "soul_urge": 6, "personality": 11, "birthday": 6,
        })

    def test_master_life_path(self):
        # 2 + 9 + (1984 -> 22) = 33
        self.assertEqual(n.core_numbers("Ann Lee", dt.date(1984, 2, 9)).life_path, 33)

    def test_birthday_keeps_masters(self):
        self.assertEqual(n.core_numbers("A", dt.date(2000, 1, 29)).birthday, 11)
        self.assertEqual(n.core_numbers("A", dt.date(2000, 1, 22)).birthday, 22)
        self.assertEqual(n.core_numbers("A", dt.date(2000, 1, 31)).birthday, 4)

    def test_accents_spaces_and_punctuation_are_ignored(self):
        plain = n.core_numbers("Jose Maria O'Neil-Lopez", dt.date(1990, 1, 1))
        accented = n.core_numbers("José María  O’Neil-López", dt.date(1990, 1, 1))
        self.assertEqual(plain, accented)

    def test_name_without_letters_is_rejected(self):
        with self.assertRaises(ValueError):
            n.core_numbers("1234 !!", dt.date(1990, 1, 1))

    def test_every_possible_number_has_a_meaning(self):
        for number in list(range(1, 10)) + [11, 22, 33]:
            title, text = n.meaning(number)
            self.assertTrue(title and text, number)


if __name__ == "__main__":
    unittest.main()


class NameEdgeCasesTest(unittest.TestCase):
    def test_y_is_the_vowel_in_a_part_without_other_vowels(self):
        # LYNN: Y is the vowel (7); L3 N5 N5 = 13 -> 4
        numbers = n.core_numbers("Lynn", dt.date(1990, 1, 1))
        self.assertEqual((numbers.soul_urge, numbers.personality), (7, 4))

    def test_y_stays_a_consonant_next_to_other_vowels(self):
        vowels, consonants = n.split_vowels("Mary Lynn")
        self.assertEqual((vowels, consonants), ("AY", "MRYLNN"))

    def test_empty_group_is_not_active_not_blank(self):
        numbers = n.core_numbers("Aia Eo", dt.date(1990, 1, 1))
        self.assertEqual(numbers.personality, 0)
        title, text = n.meaning(0)
        self.assertEqual(title, "Not active")
        self.assertTrue(text)
