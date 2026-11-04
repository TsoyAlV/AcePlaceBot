import unittest
from datetime import date
from app.date_picker import month_keyboard,parse_month


class DatePickerTests(unittest.TestCase):
    def test_leap_february_and_past_dates(self):
        markup=month_keyboard('cli:cal:token',date(2028,2,1),date(2028,2,10),'ru')
        callbacks=[b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertIn('cli:cal:token:day:2028-02-29',callbacks)
        self.assertNotIn('cli:cal:token:day:2028-02-09',callbacks)
        self.assertNotIn('cli:cal:token:month:2028-01',callbacks)
        self.assertIn('Февраль',markup.inline_keyboard[0][0].text)
        self.assertEqual([b.text for b in markup.inline_keyboard[1]],['Пн','Вт','Ср','Чт','Пт','Сб','Вс'])

    def test_korean_and_year_boundary_fit_telegram_callback_limit(self):
        markup=month_keyboard('adm:cal:012345678901:start',date(2026,12,1),date(2026,11,4),'ko')
        buttons=[b for row in markup.inline_keyboard for b in row]
        self.assertIn('2026년 12월',buttons[0].text)
        self.assertIn('adm:cal:012345678901:start:month:2027-01',[b.callback_data for b in buttons])
        self.assertTrue(all(len(b.callback_data.encode('utf-8'))<=64 for b in buttons))

    def test_month_validation(self):
        self.assertEqual(parse_month('2026-11'),date(2026,11,1))
        with self.assertRaises(ValueError):
            parse_month('2026-13')
