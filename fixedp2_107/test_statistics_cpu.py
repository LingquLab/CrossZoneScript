import unittest
from statistics_cpu import aggregate, select


class Statistics(unittest.TestCase):
    def test_same_core_composition_and_denominators(self):
        first, second = {0: 10, 1: 2}, {0: 1, 1: 20}
        composed = {c: first[c] + second[c] for c in first}
        result = select(composed, {0, 1})
        self.assertEqual(result['mean_ticks'], 16.5)
        self.assertEqual(result['max_ticks'], 22)
        self.assertEqual(result['slowest_cores'], [1])
        self.assertEqual(select(composed, {4, 5})['status'], 'NOT_EXECUTED')
        self.assertIsNone(select(composed, {4, 5})['mean_ticks'])

    def test_actual_ties_and_changing_denominator(self):
        a, b = select({0: 30, 1: 30}, {0, 1}), select({0: 1, 1: 30}, {0, 1})
        r = aggregate([(1, 0, a), (2, 0, b)])
        self.assertEqual(r['mean_ticks'], 22.75)
        self.assertEqual(r['slowest'], [{'round': 1, 'rank': 0, 'cores': [0, 1]},
                                        {'round': 2, 'rank': 0, 'cores': [1]}])
        with self.assertRaises(ValueError):
            aggregate([(1, 0, a), (2, 0, select({0: 1}, {0}))])


if __name__ == '__main__':
    unittest.main()
