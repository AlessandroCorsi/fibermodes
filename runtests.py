#!/usr/bin/env python3

import unittest

if __name__ == '__main__':
    loader = unittest.TestLoader()
    tests = loader.discover('tests', '*.py')
    testRunner = unittest.runner.TextTestRunner()
    testRunner.run(tests)
