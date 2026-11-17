from app.services.matching_service import find_driver
from app.models.ride import Ride

def create_ride():
    driver = find_driver()
    return Ride(driver=driver)

