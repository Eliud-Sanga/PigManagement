"""Cargo-only journey workflow; a journey stays open until arrival at Ikonda."""
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from django.conf import settings
from .models import Cargo, Route, Trip, Vehicle, ZERO

HOME = 'Ikonda'
REQUIRED = frozenset({'transport.view_vehicle', 'transport.view_route', 'transport.add_route', 'transport.change_route', 'transport.add_trip', 'transport.change_trip'})


def enabled(profile):
    return bool(getattr(settings, 'BIZMANAGER_AUTO_CARGO_ROUTE', False) and profile.cargo_enabled and not profile.passenger_enabled)


def same(left, right):
    return (left or '').strip().casefold() == (right or '').strip().casefold()


@transaction.atomic
def depart(company, vehicle_id, origin, destination, notes='', *, close_previous=False):
    origin, destination = (origin or '').strip(), (destination or '').strip()
    if not origin or not destination or same(origin, destination):
        raise ValidationError('Enter different departure and destination locations.')
    if same(origin, HOME): origin = HOME
    if same(destination, HOME): destination = HOME
    if not vehicle_id:
        raise ValidationError('Select a vehicle.')
    vehicle = Vehicle.all_objects.select_for_update().get(pk=int(vehicle_id), company=company, is_archived=False)
    if not vehicle.is_active:
        raise ValidationError('Select an active vehicle.')
    routes = list(Route.all_objects.select_for_update().filter(company=company, vehicle=vehicle, is_archived=False, status__in=(Route.Status.ACTIVE, Route.Status.PLANNED))[:2])
    if len(routes) > 1:
        raise ValidationError('Resolve existing open journeys for this vehicle first.')
    if not routes:
        if not same(origin, HOME):
            raise ValidationError('A new journey must depart from Ikonda.')
        route = Route.objects.create(company=company, vehicle=vehicle, start_location=HOME, end_location='')
        route.start_route()
    else:
        route = routes[0]
        if route.status != Route.Status.ACTIVE or not same(route.start_location, HOME) or route.end_location:
            raise ValidationError('Resolve the existing manual route before starting an automatic journey.')
        last = Trip.all_objects.filter(company=company, route=route, is_archived=False).exclude(status=Trip.Status.CANCELLED).order_by('-trip_number').first()
        if last is not None and last.status == Trip.Status.IN_PROGRESS and close_previous:
            if not same(origin, last.destination):
                raise ValidationError(f'The next departure must be from {last.destination}.')
            # Arrival and new departure are one transaction, under the vehicle lock.
            arrive(company, last.pk)
            last.refresh_from_db()
            route.refresh_from_db()
            if route.status == Route.Status.COMPLETED:
                return depart(company, vehicle_id, origin, destination, notes)
        if last is None or last.status != Trip.Status.COMPLETED:
            raise ValidationError('Complete the previous trip, or resolve the interrupted journey first.')
        if not same(origin, last.destination):
            raise ValidationError(f'The next departure must be from {last.destination}.')
    trip = Trip.objects.create(company=company, route=route, origin=origin, destination=destination, notes=notes)
    trip.start_trip()
    return trip


@transaction.atomic
def arrive(company, trip_id):
    # Lock order matches departure: vehicle, route, trip.
    pointer = Trip.all_objects.get(pk=trip_id, company=company, is_archived=False)
    Vehicle.all_objects.select_for_update().get(pk=pointer.route.vehicle_id, company=company, is_archived=False)
    route = Route.all_objects.select_for_update().get(pk=pointer.route_id, company=company, is_archived=False)
    trip = Trip.all_objects.select_for_update().get(pk=trip_id, company=company, route=route, is_archived=False)
    if route.status != Route.Status.ACTIVE or not same(route.start_location, HOME) or route.end_location:
        raise ValidationError('This is not an open automatic cargo journey.')
    latest = Trip.all_objects.filter(company=company, route=route, is_archived=False).exclude(status=Trip.Status.CANCELLED).order_by('-trip_number').first()
    if latest is None or latest.pk != trip.pk:
        raise ValidationError('Confirm the current trip only.')
    trip.complete_trip()
    if same(trip.destination, HOME):
        if route._has_open_trips():
            raise ValidationError('Resolve other open trips before closing this journey.')
        route.end_location = HOME
        route.completed_at = timezone.now()
        route.status = Route.Status.COMPLETED
        route.total_income = Cargo.all_objects.filter(company=company, is_archived=False, trip__route=route).aggregate(total=Sum('agreed_amount'))['total'] or ZERO
        route.total_expenses = route.calculate_total_expenses()
        route.profit = route.total_income - route.total_expenses
        route.save(update_fields=('end_location', 'completed_at', 'status', 'total_income', 'total_expenses', 'profit', 'updated_at'))
    return trip
