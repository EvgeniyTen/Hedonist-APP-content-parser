import os
import scrapy
from pathlib import Path
import random

class RestSpider(scrapy.Spider):
    name = "rest_spider"
    
    start_urls = ['https://www.example.com']
    rest_number = 0
    custom_settings = { 
        'DOWNLOAD_DELAY': 0,
        'ROBOTSTXT_OBEY': False,
        'RETRY_HTTP_CODES': [500, 503, 504, 400, 403, 404, 408],
    }

    def parse_rest_details(self, response):
        os.makedirs('rest_pages', exist_ok=True)
        filename = f'rest_pages/{self.rest_number}.html'
        with open(filename, 'wb') as f:
            f.write(response.body)
            self.rest_number += 1


    def parse(self, response):
        rest_list_container = response.xpath("//div[@data-test-target='restaurants-list']//a[contains(@href, '/Restaurant_Review')]")
        found_restaurants = len(rest_list_container)
        print(f"Found {found_restaurants} restaurants on this page.")
        if found_restaurants == 0:
            print(f"Found 0 restaurants on the page, cannot proceed further.")
            return

        for rest_link in rest_list_container:
            rest_url = response.urljoin(rest_link.attrib['href'])
            yield response.follow(rest_url, self.parse_rest_details)

        next_page_selector = 'a[data-smoke-attr="pagination-next-arrow"]::attr(href)'
        next_page_url = response.css(next_page_selector).get()
    
        if next_page_url:
            yield response.follow(next_page_url, self.parse)
        else:
            print("No next page link found. Reached the end of pagination.")



