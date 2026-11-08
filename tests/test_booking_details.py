import unittest
from decimal import Decimal

from app import booking_details


class BookingDetailsTests(unittest.TestCase):
    def test_phone_preserves_leading_zero_and_normalizes_international_format(self):
        self.assertEqual(booking_details.phone('010-2263-6263'),'01022636263')
        self.assertEqual(booking_details.phone('+82 (10) 2263-6263'),'+821022636263')
        for value in ('123','abcdefghi','01022\n636263','++821022636263','1'*16):
            with self.subTest(value=value),self.assertRaises(ValueError):
                booking_details.phone(value)

    def test_guest_count_and_event_are_validated(self):
        for value in (0,-1,False,1.5,'20 человек','10001'):
            with self.subTest(value=value),self.assertRaises(ValueError):
                booking_details.guests(value)
        self.assertEqual(booking_details.guests('40'),40)
        self.assertEqual(booking_details.event(' Level   up '),'Level up')
        for value in ('','   ','x'*201):
            with self.assertRaises(ValueError):
                booking_details.event(value)

    def test_balance_subtracts_both_received_payment_amounts(self):
        row=dict(amount=Decimal(150000),deposit_paid_amount=Decimal(50000),balance_paid_amount=Decimal(20000),unit='KRW')
        self.assertIn('К доплате: 80 000 KRW',booking_details.payment_text(row))
        self.assertIn('잔금: 80 000 KRW',booking_details.payment_text(row,'ko'))
        self.assertEqual(booking_details.text({}),'')
