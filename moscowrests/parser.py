import re
from pprint import pprint
from parsel import Selector
from datetime import time
from tqdm import tqdm
from glob import glob
from functools import cached_property
from zoneinfo import ZoneInfo
import chompjs
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from base64 import b64decode


def extract_url(b64_url):
    raw_url = b64decode(b64_url).decode()
    try:
        cut_idx = raw_url.index('https://')
    except ValueError:
        cut_idx = raw_url.index('http://')
    url = raw_url[cut_idx:]
    parsed_url = urlparse(url)
    qs_dict = parse_qs(parsed_url.query, keep_blank_values=True)
    if 'utm_source' in qs_dict:
        del qs_dict['utm_source']
    parsed_url = parsed_url._replace(query=urlencode(qs_dict, True))
    return urlunparse(parsed_url)


def get_by_key_prefix(obj, key_prefix):
    for key in obj.keys():
        if key.startswith(key_prefix):
            return obj[key]
    raise KeyError(f'key prefix not found: {key_prefix}')

import logging
logger = logging.getLogger('parser')
logging.basicConfig()


class RestParseError(ValueError):
    pass


class RestData:
    def __init__(self, page_html):
        self.page = Selector(text=page_html)
        web_context_js = self.page.xpath('//script[contains(., "__WEB_CONTEXT__=")]/text()').get()
        cut_wc_from = web_context_js.index('window.__WEB_CONTEXT__=') + len('window.__WEB_CONTEXT__=')
        cut_wc_to = web_context_js.index(';(this.$WP=this.$WP||[])')
        data = chompjs.parse_js_object(web_context_js[cut_wc_from:cut_wc_to])
        if not data['pageManifest']['redux']['api']['responses']:
            logger.error("Object data is empty: %s", data['pageManifest']['redux']['meta']['initialAbsoluteUrl'])
            raise RestParseError("Insufficient data")

        self.api_data = data['pageManifest']['redux']['api']['responses']
        self.rest_id = None
        for key, value in self.api_data.items():
            if key.startswith('/data/1.0/restaurant/') and key.endswith('/overview'):
                self.rest_id = value['data']['detailId']
        assert self.rest_id is not None, 'cannot extract rest id'

    @cached_property
    def menu_url(self):
        header = self.page.css('[data-test-target=restaurant-detail-info]')
        encoded_links = header.css("[data-encoded-url]::attr(data-encoded-url)").getall()
        if len(encoded_links) == 2:
            return extract_url(encoded_links[1])
        return None

    @cached_property
    def web_storyboard(self):
        return self._api_resp('restaurant/{}/webStoryboard')

    @cached_property
    def storyboard(self):
        try:
            return self._api_resp('restaurant/{}/storyboard')
        except KeyError:
            return None

    @cached_property
    def owner_status(self):
        return self._api_resp('restaurant/{}/ownerStatus')

    @cached_property
    def overview(self):
        return self._api_resp('restaurant/{}/overview')

    @cached_property
    def location(self):
        return self._api_resp('location/{}')

    def _api_resp(self, fmt_string):
        key = '/data/1.0/' + fmt_string.format(self.rest_id)
        return self.api_data[key]['data']


def extract_working_time(ta_format):
    hours_dec = (ta_format * 60) / 3600 % 24
    hours = int(hours_dec)
    minutes_dec = hours_dec - hours
    minutes = int(minutes_dec * 60)
    return time(hour=hours, minute=minutes, second=0)


VEGETERIAN_FRIENDLY_ID = '10665'
VEGAN_FRIENDLY_ID = '10697'
GLUTEN_FREE_ID = '10992'

DIGIT_REGEX = re.compile(r'\d+')


def parse_rest_tripadvisor_page(html):
    try:
        rest = RestData(html)
        rating_questions = {}
        for r in rest.overview['rating']['ratingQuestions']:
            rating_questions[r['icon']] = r

        address_obj = rest.location['address_obj']

        if rest.location.get('hours'):
            working_hours_by_days = []
            timezone = rest.location['hours']['timezone']
            zone_info = ZoneInfo(timezone)
            for week_day in rest.location['hours']['week_ranges']:
                if not week_day:
                    working_hours_by_days.append(None)
                    continue
                open_time = extract_working_time(week_day[0]['open_time']).replace(tzinfo=zone_info)
                close_time = extract_working_time(week_day[0]['close_time']).replace(tzinfo=zone_info)
                working_hours_by_days.append((open_time.isoformat(), close_time.isoformat()))
        else:
            working_hours_by_days = None

        dietary_restrictions = {}
        for v in rest.location['dietary_restrictions']:
            dietary_restrictions[v['key']] = v['name']

        cuisines = {}
        for c in rest.location['cuisine']:
            if c['key'] not in dietary_restrictions:
                cuisines[c['key']] = c['name']

        if rest.location['photo']:
            photo_images = rest.location['photo']['images']
        else:
            photo_images = None

        if rest.overview['detailCard']['numericalPrice']:
            from_price_string, to_price_string = rest.overview['detailCard']['numericalPrice'].split(' - ')
            assert from_price_string.endswith('руб')
            assert to_price_string.endswith('руб')
            price_currency = 'RUB'
            from_price = int(''.join(DIGIT_REGEX.findall(from_price_string)))
            to_price = int(''.join(DIGIT_REGEX.findall(to_price_string)))
        else:
            from_price = None
            to_price = None
            price_currency = None

        if 'award' in rest.overview:
            award = rest.overview['award']['awardText']
        else:
            award = None

        eating_times = {}
        for et in rest.overview['detailCard']['tagTexts']['meals']['tags']:
            eating_times[et['tagId']] = et['tagValue']

        features = {}
        for et in rest.overview['detailCard']['tagTexts']['features']['tags']:
            features[et['tagId']] = et['tagValue']

        landmark = rest.overview['location']['landmark']
        if landmark:
            landmark_distance_string, landmark_object_string = landmark.split('от:')
            landmark_object_string = landmark_object_string.strip()
            landmark_distance_string = landmark_distance_string.replace('<b>', '')
            landmark_distance_string = landmark_distance_string.replace('</b>', '')
            landmark_distance_string = landmark_distance_string.replace(',', '.').strip()
            landmark_distance = float(landmark_distance_string.replace('км', '').strip()) * 1000
            landmark = (landmark_object_string, int(landmark_distance))
        else:
            landmark = None

        price_level_string = rest.location['price_level']
        if not price_level_string:
            price_level_from = None
            price_level_to = None
        else:
            price_level_range = price_level_string.split('-')
            if len(price_level_range) == 2:
                price_level_from = price_level_range[0].count("$")
                price_level_to = price_level_range[1].count("$")
            else:
                assert len(price_level_range) == 1
                price_level_from = price_level_range[0].count('$')
                price_level_to = price_level_from

        result = {
            'name': rest.overview['name'].split(', Россия')[0].strip(),
            'registered_at_tripadvisor': rest.owner_status['isVerified'],
            'rating': float(rest.overview['rating']['primaryRating']),
            'rating_food': rating_questions['restaurants']['rating'] / 10 if rating_questions.get('restaurants') else None,
            'rating_price_quality': rating_questions['wallet-fill']['rating'] / 10 if rating_questions.get('wallet-fill') else None,
            'rating_service': rating_questions['bell']['rating'] / 10 if rating_questions.get('bell') else None,
            'city': address_obj['city'],
            'address': address_obj['street1'],
            'zipcode': address_obj['postalcode'],
            'country': address_obj['country'],
            'latitude': rest.overview['location']['latitude'],
            'longitude': rest.overview['location']['longitude'],
            'neighborhood': rest.overview['location']['neighborhood'],
            'email': rest.overview['contact']['email'],
            'tel': rest.overview['contact']['phone'],
            'tripadvisor_url': rest.location['web_url'],
            'menu_url': rest.menu_url,
            'working_hours_by_days': working_hours_by_days,
            'price_level_from': price_level_from,
            'price_level_to': price_level_to,
            'dietary_restrictions': list(dietary_restrictions.values()),
            'gluten_free_dishes': GLUTEN_FREE_ID in dietary_restrictions,
            'vegetarian_friendly': VEGETERIAN_FRIENDLY_ID in dietary_restrictions,
            'vegan_friendly': VEGAN_FRIENDLY_ID in dietary_restrictions,
            'cuisines': list(cuisines.values()),
            'price_range_min': from_price,
            'price_range_max': to_price,
            'price_range_currency': price_currency,
            'reviews_count': int(rest.location['num_reviews']),
            'award': award,
            'description': rest.location['description'],
            'eating_times': list(eating_times.values()),
            'features': list(features.values()),
            'landmark': landmark,
        }

        if result['rating'] < 0:
            result['rating'] = None

        if rest.overview['contact']['website']:
            result['website'] = extract_url(rest.overview['contact']['website'])

        if rest.overview['rating']['primaryRanking']:
            result['rank'] = rest.overview['rating']['primaryRanking']['rank']
            result['rank_total_count'] = rest.overview['rating']['primaryRanking']['totalCount']
        else:
            result['rank'] = None
            result['rank_total_count'] = None

        if photo_images:
            if photo_images.get('original'):
                result['photo_urls'] = [photo_images['original']['url']]
            else:
                result['photo_urls'] = [photo_images['large']['url']]
        if rest.storyboard:
            result['video_url'] = rest.storyboard['storyboardUrl']
        return result
    except Exception as exc:
        if isinstance(exc, RestParseError):
            raise
        import traceback
        print(traceback.format_exc())
        import pdb; pdb.set_trace()
        pass


if __name__ == "__main__":
    files = glob('rest_pages/*.html')
    total = len(files)
    import json
    x = 0
    with open('moscow_rests.jl', 'w') as f:
        for filename in tqdm(files, total=total):
            x += 1
            with open(filename) as sf:
                try:
                    data = parse_rest_tripadvisor_page(sf.read())
                    data['id'] = x
                    f.write(json.dumps(data, ensure_ascii=False) + '\n')
                except RestParseError:
                    pass