from django.http import Http404, HttpResponseRedirect, HttpResponse, HttpResponseBadRequest
from django.shortcuts import render_to_response, get_object_or_404, redirect
from django.template import RequestContext, loader
from django.template.loader import render_to_string
from django.core.urlresolvers import reverse
from django.core.exceptions import PermissionDenied
from django.contrib.auth.decorators import user_passes_test, login_required
from django.contrib import messages
from django.db.models import Sum, Count
from django.conf import settings
from django.views.decorators.cache import cache_page
from ipware.ip import get_real_ip

from debate.result import BallotSet
from debate import forms
from debate.models import *

from django.forms.models import modelformset_factory, formset_factory
from django.forms import Textarea

import datetime
from functools import wraps
import json

def get_ip_address(request):
    ip = get_real_ip(request)
    if ip is None:
        return "0.0.0.0"
    return ip

def decide_show_draw_strength(tournament):
    return tournament.config.get('team_standings_rule') == "nz"

def redirect_round(to, round, **kwargs):
    return redirect(to, tournament_slug=round.tournament.slug,
                    round_seq=round.seq, *kwargs)

def redirect_tournament(to, tournament, **kwargs):
    return redirect(to, tournament_slug=tournament.slug, **kwargs)

def tournament_view(view_fn):
    @wraps(view_fn)
    def foo(request, tournament_slug, *args, **kwargs):
        return view_fn(request, request.tournament, *args, **kwargs)
    return foo

def round_view(view_fn):
    @wraps(view_fn)
    @tournament_view
    def foo(request, tournament, round_seq, *args, **kwargs):
        return view_fn(request, request.round, *args, **kwargs)
    return foo

def public_optional_tournament_view(config_option):
    def bar(view_fn):
        @wraps(view_fn)
        @tournament_view
        def foo(request, tournament, *args      , **kwargs):
            if tournament.config.get(config_option):
                return view_fn(request, tournament, *args, **kwargs)
            else:
                return redirect_tournament('public_index', tournament)
        return foo
    return bar

def public_optional_round_view(config_option):
    def bar(view_fn):
        @wraps(view_fn)
        @round_view
        def foo(request, round, *args, **kwargs):
            if round.tournament.config.get(config_option):
                return view_fn(request, round, *args, **kwargs)
            else:
                return redirect_tournament('public_index', round.tournament)
        return foo
    return bar

def admin_required(view_fn):
    return user_passes_test(lambda u: u.is_superuser)(view_fn)


def expect_post(view_fn):
    @wraps(view_fn)
    def foo(request, *args, **kwargs):
        if request.method != "POST":
            return HttpResponseBadRequest("Expected POST")
        return view_fn(request, *args, **kwargs)
    return foo


def r2r(request, template, extra_context=None):
    rc = RequestContext(request)
    if extra_context:
        rc.update(extra_context)
    return render_to_response(template, context_instance=rc)


def index(request):
    tournaments = Tournament.objects.all()
    return r2r(request, 'site_index.html', dict(tournaments=Tournament.objects.all()))

## Public UI

PUBLIC_PAGE_CACHE_TIMEOUT   = 60 * 10    # 10 Minutes
TAB_PAGES_CACHE_TIMEOUT     = 60 * 120   # 120 Minutes

@cache_page(10) # Set slower to show new indexes so it will show new pages
@tournament_view
def public_index(request, t):
    return r2r(request, 'public/public_tournament_index.html')


@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_participants')
def public_participants(request, t):
    adjs = Adjudicator.objects.all().select_related('institution')
    speakers = Speaker.objects.all().select_related('team','team__institution')
    return r2r(request, "public/public_participants.html", dict(adjs=adjs, speakers=speakers))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_draw')
def public_draw(request, t):
    r = t.current_round
    if r.draw_status == r.STATUS_RELEASED:
        draw = r.get_draw()
        return r2r(request, "public/public_draw_released.html", dict(draw=draw, round=r))
    else:
        return r2r(request, 'public/public_draw_unreleased.html', dict(draw=None, round=r))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_round_view('show_all_draws')
def public_draw_by_round(request, round):
    if round.draw_status == round.STATUS_RELEASED:
        draw = round.get_draw()
        return r2r(request, "public/public_draw_released.html", dict(draw=draw, round=round))
    else:
        return r2r(request, 'public/public_draw_unreleased.html', dict(draw=None, round=round))


@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_team_standings')
def public_team_standings(request, t):
    if t.release_all:
        # Assume that the time "release all" is used, the current round
        # is the last round.
        round = t.current_round
    else:
        round = t.current_round.prev

    # Find the most recent non-silent preliminary round
    while round is not None and (round.silent or round.stage != Round.STAGE_PRELIMINARY):
        round = round.prev

    if round is not None and round.silent is False:

        from debate.models import TeamScore

        # Ranking by institution__name and reference isn't the same as ordering by
        # short_name, which is what we really want. But we can't rank by short_name,
        # because it's not a field (it's a property). So we'll do this in JavaScript.
        # The real purpose of this ordering is to obscure the *true* ranking of teams
        # - teams are not supposed to know rankings between teams on the same number
        # of wins.
        teams = Team.objects.order_by('institution__code', 'reference')
        rounds = t.prelim_rounds(until=round).filter(silent=False).order_by('seq')

        def get_round_result(team, r):
            try:
                ts = TeamScore.objects.get(
                    ballot_submission__confirmed=True,
                    debate_team__team=team,
                    debate_team__debate__round=r,
                )
                ts.opposition = ts.debate_team.opposition.team
                return ts
            except TeamScore.DoesNotExist:
                return None

        for team in teams:
            team.round_results = [get_round_result(team, r) for r in rounds]
            # Do this manually, in case there are silent rounds
            team.wins = [ts.win for ts in team.round_results if ts].count(True)
            team.points = sum([ts.points for ts in team.round_results if ts])


        return r2r(request, 'public/public_team_standings.html', dict(teams=teams, rounds=rounds, round=round))
    else:
        return r2r(request, 'public/index.html')

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_results')
def public_break_index(request, t):
    return r2r(request, "public/public_break_index.html")

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_breaking_teams')
def public_breaking_teams(request, t, name, category):
    teams = Team.objects.breaking_teams(t, category)
    return r2r(request, 'public/public_breaking_teams.html', dict(teams=teams, category=category, name=name))

@admin_required
@tournament_view
def breaking_teams(request, t, name, category):
    teams = Team.objects.breaking_teams(t, category)
    return r2r(request, 'breaking_teams.html', dict(teams=teams, category=category, name=name))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_breaking_adjs')
def public_breaking_adjs(request, t):
    adjs = Adjudicator.objects.filter(breaking=True, tournament=t).select_related('institution')
    return r2r(request, 'public/public_breaking_adjudicators.html', dict(adjs=adjs))

@admin_required
@tournament_view
def breaking_adjs(request, t):
    adjs = Adjudicator.objects.filter(breaking=True, tournament=t).select_related('institution')
    return r2r(request, 'breaking_adjudicators.html', dict(adjs=adjs))


@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_ballots')
def public_ballot_submit(request, t):
    r = t.current_round

    das = DebateAdjudicator.objects.filter(debate__round=r).select_related('adjudicator', 'debate')

    if r.draw_status == r.STATUS_RELEASED and r.motions_good_for_public:
        draw = r.get_draw()
        return r2r(request, 'public/public_add_ballot.html', dict(das=das))
    else:
        return r2r(request, 'public/public_add_ballot_unreleased.html', dict(das=None, round=r))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_feedback')
def public_feedback_submit(request, t):
    adjudicators = Adjudicator.objects.all()
    teams = Team.objects.all()
    return r2r(request, 'public/public_add_feedback.html', dict(adjudicators=adjudicators, teams=teams))


@cache_page(3) # short cache - needs to update often
@public_optional_tournament_view('feedback_progress')
def public_feedback_progress(request, t):
    def calculate_coverage(submitted, total):
        if total == 0:
            return False # Don't show these ones
        elif submitted == 0:
            return 0
        else:
            return int((float(submitted) / float(total)) * 100)

    feedback = AdjudicatorFeedback.objects.all()
    adjudicators = Adjudicator.objects.all()
    teams = Team.objects.all().select_related('institution')
    current_round = request.tournament.current_round.seq

    for adj in adjudicators:
        adj.total_ballots = 0
        adj.submitted_feedbacks = feedback.filter(source_adjudicator__adjudicator = adj)
        adjudications = DebateAdjudicator.objects.filter(adjudicator = adj)

        for item in adjudications:
            # Finding out the composition of their panel, tallying owed ballots
            if item.type == item.TYPE_CHAIR:
                adj.total_ballots += len(item.debate.adjudicators.trainees)
                adj.total_ballots += len(item.debate.adjudicators.panel)

            if item.type == item.TYPE_PANEL:
                # Panelists owe on chairs
                adj.total_ballots += 1

            if item.type == item.TYPE_TRAINEE:
                # Trainees owe on chairs
                adj.total_ballots += 1

        adj.submitted_ballots = max(adj.submitted_feedbacks.count(), 0)
        adj.owed_ballots = max((adj.total_ballots - adj.submitted_ballots), 0)
        adj.coverage = min(calculate_coverage(adj.submitted_ballots, adj.total_ballots), 100)

    for team in teams:
        team.submitted_ballots = max(feedback.filter(source_team__team = team).count(), 0)
        team.owed_ballots = max((current_round - team.submitted_ballots), 0)
        team.coverage = min(calculate_coverage(team.submitted_ballots, current_round), 100)

    return r2r(request, 'public/public_feedback_tab.html', dict(teams=teams, adjudicators=adjudicators))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_motions')
def public_motions(request, t):
    order_by = t.config.get('public_motions_descending') and '-seq' or 'seq'
    rounds = Round.objects.filter(motions_released=True, tournament=t).order_by(order_by)
    return r2r(request, 'public/public_motions.html', dict(rounds=rounds))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_divisions')
def public_divisions(request, t):
    divisions = Division.objects.filter(tournament=t).all().select_related('venue_group')
    divisions = sorted(divisions, key=lambda x: float(x.name))
    venue_groups = set(d.venue_group for d in divisions)
    for uvg in venue_groups:
        uvg.divisions = [d for d in divisions if d.venue_group == uvg]

    return r2r(request, 'public/public_divisions.html', dict(venue_groups=venue_groups))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@tournament_view
def all_tournaments_all_venues(request, t):
    venues = VenueGroup.objects.all()
    return r2r(request, 'public/public_all_tournament_venues.html', dict(venues=venues))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@tournament_view
def all_draws_for_venue(request, t, venue_id):
    venue_group = VenueGroup.objects.get(pk=venue_id)
    debates = Debate.objects.filter(division__venue_group=venue_group).select_related(
        'round','round__tournament','division')
    return r2r(request, 'public/public_all_draws_for_venue.html', dict(
        venue_group=venue_group, debates=debates))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@tournament_view
def all_tournaments_all_institutions(request, t):
    institutions = Institution.objects.all()
    return r2r(request, 'public/public_all_tournament_institutions.html', dict(
        institutions=institutions))

@tournament_view
def all_draws_for_institution(request, t, institution_id):
    institution = Institution.objects.get(pk=institution_id)
    debate_teams = DebateTeam.objects.filter(team__institution=institution).select_related(
        'debate', 'debate__division', 'debate__division__venue_group', 'debate__round')
    debates = [dt.debate for dt in debate_teams]

    return r2r(request, 'public/public_all_draws_for_institution.html', dict(
        institution=institution, debates=debates))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@tournament_view
def all_tournaments_all_teams(request, t):
    teams = Team.objects.filter(tournament__active=True).select_related('institution','tournament').prefetch_related('division')
    return r2r(request, 'public/public_all_tournament_teams.html', dict(
        teams=teams))


@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@tournament_view
def public_all_draws(request, t):
    all_rounds = list(Round.objects.filter(tournament=t))
    for r in all_rounds:
        r.draw = r.get_draw()

    return r2r(request, 'public/public_draw_display_all.html', dict(
        all_rounds=all_rounds))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_side_allocations')
def public_side_allocations(request, t):
    teams = Team.objects.filter(tournament=t).select_related('institution')
    rounds = Round.objects.filter(tournament=t).order_by("seq")
    tpas = dict()
    TPA_MAP = {
        TeamPositionAllocation.POSITION_AFFIRMATIVE: "Aff",
        TeamPositionAllocation.POSITION_NEGATIVE: "Neg",
    }
    for tpa in TeamPositionAllocation.objects.all():
        tpas[(tpa.team.id, tpa.round.seq)] = TPA_MAP[tpa.position]
    for team in teams:
        team.side_allocations = [tpas.get((team.id, round.id), "-") for round in rounds]
    return r2r(request, "public/public_side_allocations.html", dict(teams=teams, rounds=rounds))

## Tab

@cache_page(TAB_PAGES_CACHE_TIMEOUT)
@public_optional_tournament_view('tab_released')
def public_team_tab(request, t):
    round = t.current_round
    from debate.models import TeamScore
    teams = Team.objects.ranked_standings(round)

    rounds = t.prelim_rounds(until=round).order_by('seq')

    def get_round_result(team, r):
        try:
            ts = TeamScore.objects.get(
                ballot_submission__confirmed=True,
                debate_team__team=team,
                debate_team__debate__round=r,
            )
            ts.opposition = ts.debate_team.opposition.team
            return ts
        except TeamScore.DoesNotExist:
            return None

    def get_score(team, r):
        try:
            ts = TeamScore.objects.get(
                ballot_submission__confirmed=True,
                debate_team__team=team,
                debate_team__debate__round=r,
            )
            opposition = ts.debate_team.opposition.team
            debate_id = ts.debate_team.debate.id
            return ts.score, ts.points, opposition, debate_id
        except TeamScore.DoesNotExist:
            return None

    for team in teams:
        team.results_in = True # always
        team.scores = [get_score(team, r) for r in rounds]
        team.round_results = [get_round_result(team, r) for r in rounds]
        team.wins = [ts.win for ts in team.round_results if ts].count(True)
        team.points = sum([ts.points for ts in team.round_results if ts])

    show_ballots = round.tournament.config.get('ballots_released')

    return r2r(request, 'public/public_team_tab.html', dict(teams=teams,
            rounds=rounds, round=round, show_ballots=show_ballots))



@cache_page(TAB_PAGES_CACHE_TIMEOUT)
@public_optional_tournament_view('motion_tab_released')
def public_motions_tab(request, t):
    round = t.current_round
    rounds = t.prelim_rounds(until=round).order_by('seq')
    print rounds
    motions = list()
    motions = Motion.objects.statistics(round=round)
    return r2r(request, 'public/public_motions_tab.html', dict(motions=motions))


@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('ballots_released')
def public_ballots_view(request, t, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    if debate.result_status != Debate.STATUS_CONFIRMED:
        raise Http404()

    round = debate.round
    # Can't see results for current round or later
    if (round.seq > round.tournament.current_round.seq and not round.tournament.release_all) or round.silent:
        raise Http404()

    ballot_submission = debate.confirmed_ballot
    if ballot_submission is None:
        raise Http404()

    ballot_set = BallotSet(ballot_submission)
    return r2r(request, 'public/public_ballot_set.html', dict(debate=debate, ballot_set=ballot_set))

@login_required
@tournament_view
def tournament_home(request, t):
    # Actions
    from debate.models import ActionLog
    a = ActionLog.objects.filter(tournament=t).order_by('-id')[:20].select_related(
        'user', 'debate', 'ballot_submission'
    )

    # Speaker Scores
    from debate.models import SpeakerScore
    round = t.current_round
    # This should never happen, but if it does, fail semi-gracefully
    if round is None:
        if request.user.is_superuser:
            return HttpResponseBadRequest("You need to set the current round. <a href=\"/admin/debate/tournament\">Go to Django admin.</a>")
        else:
            raise Http404()

    rounds = t.prelim_rounds(until=round).order_by('seq')

    # Draw Status
    draw = round.get_draw()
    stats = {
        'none': draw.filter(result_status=Debate.STATUS_NONE).count(),
        'draft': draw.filter(result_status=Debate.STATUS_DRAFT).count(),
        'confirmed': draw.filter(result_status=Debate.STATUS_CONFIRMED).count(),
    }
    stats['in'] = stats['confirmed']
    stats['out'] = stats['none'] + stats['draft']
    if (stats['out'] + stats['in']) > 0:
        stats['pc'] = int(float(stats['in']) / (stats['out'] + stats['in']) * 100)
    else:
        stats['pc'] = 0

    return r2r(request, 'tournament_home.html', dict(stats=stats, round=round, actions=a))

@admin_required
@tournament_view
def tournament_config(request, t):
    from debate.config import make_config_form

    context = {}
    if request.method == 'POST':
        form = make_config_form(t, request.POST)
        if form.is_valid():
            form.save()
            context['updated'] = True
            ActionLog.objects.log(type=ActionLog.ACTION_TYPE_CONFIG_EDIT, user=request.user, tournament=t)
    else:
        form = make_config_form(t)

    context['form'] = form

    return r2r(request, 'tournament_config.html', context)


@admin_required
@tournament_view
def feedback_progress(request, t):
    def calculate_coverage(submitted, total):
        if total == 0 or submitted == 0:
            return 0 # avoid divide-by-zero error
        else:
            return int((float(submitted) / float(total)) * 100)

    from debate.models import AdjudicatorFeedback
    feedback = AdjudicatorFeedback.objects.select_related('source_adjudicator__adjudicator','source_team__team').all()
    adjudicators = Adjudicator.objects.all()
    adjudications = DebateAdjudicator.objects.select_related('adjudicator','debate').all()
    teams = Team.objects.select_related('institution').all()

    # Teams only owe feedback on non silent rounds
    rounds_owed = request.tournament.rounds.filter(silent=False,
        draw_status=request.tournament.current_round.STATUS_RELEASED).count()

    for adj in adjudicators:
        adj.total_ballots = 0
        adj.submitted_feedbacks = feedback.filter(source_adjudicator__adjudicator = adj)
        adjudications = adjudications.filter(adjudicator = adj)

        for item in adjudications:
            # Finding out the composition of their panel, tallying owed ballots
            if item.type == item.TYPE_CHAIR:
                adj.total_ballots += len(item.debate.adjudicators.trainees)
                adj.total_ballots += len(item.debate.adjudicators.panel)

            if item.type == item.TYPE_PANEL:
                # Panelists owe on chairs
                adj.total_ballots += 1

            if item.type == item.TYPE_TRAINEE:
                # Trainees owe on chairs
                adj.total_ballots += 1

        adj.submitted_ballots = max(adj.submitted_feedbacks.count(), 0)
        adj.owed_ballots = max((adj.total_ballots - adj.submitted_ballots), 0)
        adj.coverage = min(calculate_coverage(adj.submitted_ballots, adj.total_ballots), 100)

    for team in teams:
        team.submitted_ballots = max(feedback.filter(source_team__team = team).count(), 0)
        team.owed_ballots = max((rounds_owed - team.submitted_ballots), 0)
        team.coverage = min(calculate_coverage(team.submitted_ballots, rounds_owed), 100)

    return r2r(request, 'wall_of_shame.html', dict(teams=teams, adjudicators=adjudicators))


@admin_required
@tournament_view
def draw_index(request, t):
    return r2r(request, 'draw_index.html')

@admin_required
@round_view
def round_index(request, round):
    return r2r(request, 'round_index.html')

@admin_required
@round_view
def round_increment_check(request, round):
    if round != request.tournament.current_round: # doesn't make sense if not current round
        raise Http404()
    num_unconfirmed = round.get_draw().filter(result_status__in=[Debate.STATUS_NONE, Debate.STATUS_DRAFT]).count()
    increment_ok = num_unconfirmed == 0
    return r2r(request, "round_increment_check.html", dict(num_unconfirmed=num_unconfirmed, increment_ok=increment_ok))

@admin_required
@expect_post
@round_view
def round_increment(request, round):
    if round != request.tournament.current_round: # doesn't make sense if not current round
        raise Http404()
    request.tournament.advance_round()
    return redirect_round('draw', request.tournament.current_round )

# public (for barcode checkins)
@round_view
def checkin(request, round):
    context = {}
    if request.method == 'POST':
        v = request.POST.get('barcode_id')
        try:
            barcode_id = int(v)
            p = Person.objects.get(barcode_id=barcode_id)
            ch, created = Checkin.objects.get_or_create(
                person = p,
                round = round
            )
            context['person'] = p

        except (ValueError, Person.DoesNotExist):
            context['unknown_id'] = v

    return r2r(request, 'checkin.html', context)

# public (for barcode checkins)
# public
@round_view
def post_checkin(request, round):
    v = request.POST.get('barcode_id')
    try:
        barcode_id = int(v)
        p = Person.objects.get(barcode_id=barcode_id)
        ch, created = Checkin.objects.get_or_create(
            person = p,
            round = round
        )

        message = p.checkin_message

        if not message:
            message = "Checked in %s" % p.name
        return HttpResponse(message)

    except (ValueError, Person.DoesNotExist):
        return HttpResponse("Unknown Id: %s" % v)

def _availability(request, round, model, context_name):

    items = getattr(round, '%s_availability' % model)()

    context = {
        context_name: items,
    }

    return r2r(request, '%s_availability.html' % model, context)


@admin_required
@round_view
def availability(request, round, model, context_name):
    return _availability(request, round, model, context_name)

@round_view
def checkin_results(request, round, model, context_name):
    return _availability(request, round, model, context_name)

def _update_availability(request, round, update_method, active_model, active_attr):

    if request.POST.get('copy'):
        prev_round = Round.objects.get(tournament=round.tournament,
                                       seq=round.seq-1)

        prev_objects = active_model.objects.filter(round=prev_round)
        available_ids = [getattr(o, '%s_id' % active_attr) for o in prev_objects]
        getattr(round, update_method)(available_ids)

        return HttpResponseRedirect(request.path.replace('update/', ''))

    available_ids = [int(a.replace("check_", "")) for a in request.POST.keys()
                     if a.startswith("check_")]

    getattr(round, update_method)(available_ids)

    ACTION_TYPES = {
        ActiveVenue:       ActionLog.ACTION_TYPE_AVAIL_VENUES_SAVE,
        ActiveTeam:        ActionLog.ACTION_TYPE_AVAIL_TEAMS_SAVE,
        ActiveAdjudicator: ActionLog.ACTION_TYPE_AVAIL_ADJUDICATORS_SAVE,
    }
    if active_model in ACTION_TYPES:
        ActionLog.objects.log(type=ACTION_TYPES[active_model],
            user=request.user, round=round, tournament=round.tournament)

    return HttpResponse("ok")

@admin_required
@expect_post
@round_view
def update_availability(request, round, update_method, active_model, active_attr):
    return _update_availability(request, round, update_method, active_model, active_attr)

@expect_post
@round_view
def checkin_update(request, round, update_method, active_model, active_attr):
    return _update_availability(request, round, update_method, active_model, active_attr)


@admin_required
@round_view
def draw_display_by_venue(request, round):
    draw = round.get_draw()
    return r2r(request, "draw_display_by_venue.html", dict(round=round, draw=draw))

@admin_required
@round_view
def draw_display_by_team(request, round):
    draw = round.get_draw()
    return r2r(request, "draw_display_by_team.html", dict(draw=draw))

@admin_required
@round_view
def draw(request, round):

    if round.draw_status == round.STATUS_NONE:
        return draw_none(request, round)

    if round.draw_status == round.STATUS_DRAFT:
        return draw_draft(request, round)

    if round.draw_status == round.STATUS_CONFIRMED:
        return draw_confirmed(request, round)

    if round.draw_status == round.STATUS_RELEASED:
        return draw_confirmed(request, round)

    raise


def draw_none(request, round):
    active_teams = round.active_teams.all()
    active_venues_count = round.active_venues.count()
    if round.stage == round.STAGE_ELIMINATION:
        all_teams_count = round.tournament.config.get("break_size")
        rooms = float(all_teams_count) / 2
    else:
        all_teams_count = Team.objects.filter(tournament=round.tournament).count()
        rooms = float(active_teams.count()) / 2

    return r2r(request, "draw_none.html", dict(active_teams=active_teams,
                                               active_venues_count=active_venues_count,
                                               rooms=rooms,
                                               round=round,
                                               all_teams_count=all_teams_count))


def draw_draft(request, round):
    draw = round.get_draw_with_standings(round)
    show_draw_strength = decide_show_draw_strength(round.tournament)
    return r2r(request, "draw_draft.html", dict(draw=draw, show_draw_strength=show_draw_strength))


def draw_confirmed(request, round):
    draw = round.get_draw()
    rooms = float(round.active_teams.count()) / 2
    active_adjs = round.active_adjudicators.all()

    return r2r(request, "draw_confirmed.html", dict(draw=draw,
                                                    active_adjs=active_adjs,
                                                    rooms=rooms))




@admin_required
@round_view
def draw_print_scoresheets(request, round):
    draw = round.get_draw()
    config = round.tournament.config
    motions = Motion.objects.filter(round=round)
    return r2r(request, "printable_scoresheets.html", dict(
        draw=draw, config=config, motions=motions))


@admin_required
@round_view
def draw_print_feedback(request, round):
    draw = round.get_draw()
    return r2r(request, "printable_feedback.html", dict(draw=draw))




@admin_required
@round_view
def draw_with_standings(request, round):
    draw = round.get_draw_with_standings(round)
    show_draw_strength = decide_show_draw_strength(round.tournament)
    return r2r(request, "draw_with_standings.html", dict(draw=draw, show_draw_strength=show_draw_strength))

@admin_required
@expect_post
@round_view
def create_draw(request, round):
    round.draw()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DRAW_CREATE,
        user=request.user, round=round, tournament=round.tournament)
    return redirect_round('draw', round)

@admin_required
@expect_post
@round_view
def create_draw_with_all_teams(request, round):
    round.draw(override_team_checkins=True)
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DRAW_CREATE,
        user=request.user, round=round, tournament=round.tournament)
    return redirect_round('draw', round)

@admin_required
@expect_post
@round_view
def confirm_draw(request, round):

    if round.draw_status != round.STATUS_DRAFT:
        return HttpResponseBadRequest("Draw status is not DRAFT")

    round.draw_status = round.STATUS_CONFIRMED
    round.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DRAW_CONFIRM,
        user=request.user, round=round, tournament=round.tournament)

    return redirect_round('draw', round)


@admin_required
@expect_post
@round_view
def release_draw(request, round):
    if round.draw_status != round.STATUS_CONFIRMED:
        return HttpResponseBadRequest("Draw status is not CONFIRMED")

    round.draw_status = round.STATUS_RELEASED
    round.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DRAW_RELEASE,
        user=request.user, round=round, tournament=round.tournament)

    return redirect_round('draw', round)


@admin_required
@expect_post
@round_view
def unrelease_draw(request, round):
    if round.draw_status != round.STATUS_RELEASED:
        return HttpResponseBadRequest("Draw status is not RELEASED")

    round.draw_status = round.STATUS_CONFIRMED
    round.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DRAW_UNRELEASE,
        user=request.user, round=round, tournament=round.tournament)

    return redirect_round('draw', round)


@admin_required
@tournament_view
def side_allocations(request, t):
    teams = Team.objects.filter(tournament=t)
    rounds = Round.objects.filter(tournament=t).order_by("seq")
    tpas = dict()
    TPA_MAP = {
        TeamPositionAllocation.POSITION_AFFIRMATIVE: "Aff",
        TeamPositionAllocation.POSITION_NEGATIVE: "Neg",
        None: "-"
    }
    for tpa in TeamPositionAllocation.objects.all():
        tpas[(tpa.team.id, tpa.round.seq)] = TPA_MAP[tpa.position]
    for team in teams:
        team.side_allocations = [tpas.get((team.id, round.id), "-") for round in rounds]
    return r2r(request, "side_allocations.html", dict(teams=teams, rounds=rounds))


@admin_required
@tournament_view
def division_allocations(request, t):
    teams = Team.objects.filter(tournament=t).all()
    divisions = Division.objects.filter(tournament=t).all()
    divisions = sorted(divisions, key=lambda x: float(x.name))
    venue_groups = VenueGroup.objects.all()

    return r2r(request, "division_allocations.html", dict(teams=teams, divisions=divisions, venue_groups=venue_groups))


@admin_required
@expect_post
@tournament_view
def save_divisions(request, t):
    culled_dict = dict((int(k), int(v)) for k, v in request.POST.iteritems() if v)

    teams = Team.objects.in_bulk([t_id for t_id in culled_dict.keys()])
    divisions = Division.objects.in_bulk([d_id for d_id in culled_dict.values()])

    for team_id, division_id in culled_dict.iteritems():
        teams[team_id].division = divisions[division_id]
        teams[team_id].save()

    # ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DIVISIONS_SAVE,
    #     user=request.user, tournament=t)

    return HttpResponse("ok")

@admin_required
@expect_post
@tournament_view
def create_division_allocation(request, t):
    from debate.division_allocator import DivisionAllocator

    teams = list(Team.objects.filter(tournament=t))
    for team in teams:
        preferences = list(TeamVenuePreference.objects.filter(team=team))
        team.preferences_dict = dict((p.priority, p.venue_group) for p in preferences)

    # Delete all existing divisions - this shouldn't affect teams (on_delete=models.SET_NULL))
    divisions = Division.objects.filter(tournament=t).delete()

    venue_groups = VenueGroup.objects.all()

    alloc = DivisionAllocator(teams=teams, divisions=divisions,venue_groups=venue_groups, tournament=t)
    success = alloc.allocate()

    if success:
        return HttpResponse("ok")
    else:
        return HttpResponseBadRequest("Couldn't create divisions")

@admin_required
@expect_post
@round_view
def create_adj_allocation(request, round):

    if round.draw_status == round.STATUS_RELEASED:
        return HttpResponseBadRequest("Draw is already released, unrelease draw to redo auto-allocation.")
    if round.draw_status != round.STATUS_CONFIRMED:
        return HttpResponseBadRequest("Draw is not confirmed, confirm draw to run auto-allocation.")

    from debate.adjudicator.hungarian import HungarianAllocator
    round.allocate_adjudicators(HungarianAllocator)

    return _json_adj_allocation(round.get_draw(), round.unused_adjudicators())


@admin_required
@expect_post
@round_view
def update_debate_importance(request, round):
    id = int(request.POST.get('debate_id'))
    im = int(request.POST.get('value'))
    debate = Debate.objects.get(pk=id)
    debate.importance = im
    debate.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_DEBATE_IMPORTANCE_EDIT,
            user=request.user, debate=debate, tournament=round.tournament)
    return HttpResponse(im)


@admin_required
@round_view
def motion_standings(request, round, for_print=False):
    rounds = round.tournament.prelim_rounds(until=round).order_by('seq')
    motions = list()
    motions = Motion.objects.statistics(round=round)
    return r2r(request, 'motions.html', dict(motions=motions))

@admin_required
@round_view
def motions(request, round):
    motions = list()
    motions = Motion.objects.statistics(round=round)
    if len(motions) > 0:
        motions = [m for m in motions if m.round == round]

    return r2r(request, "motions.html", dict(motions=motions))

@admin_required
@round_view
def motions_edit(request, round):
    MotionFormSet = modelformset_factory(Motion,
        can_delete=True, extra=3, exclude=['round'])

    if request.method == 'POST':
        formset = MotionFormSet(request.POST, request.FILES)
        if formset.is_valid():
            for motion in formset.save(commit=False):
                motion.round = round
                motion.save()
                ActionLog.objects.log(type=ActionLog.ACTION_TYPE_MOTION_EDIT,
                    user=request.user, motion=motion, tournament=round.tournament)
            if 'submit' in request.POST:
                return redirect_round('motions', round)
    else:
        formset = MotionFormSet(queryset=Motion.objects.filter(round=round))

    return r2r(request, "motions_edit.html", dict(formset=formset))


@admin_required
@round_view
def motions_assign(request, round):

    from django.forms import ModelForm
    from django.forms.widgets import CheckboxSelectMultiple
    from django.forms.models import ModelMultipleChoiceField

    class MyModelChoiceField(ModelMultipleChoiceField):
        def label_from_instance(self, obj):
            return "%s %s - Division %s @ %s" % (
                obj.venue_group.short_name.split(' ')[2],
                obj.venue_group.short_name.split(' ')[1],
                obj.name,
                obj.venue_group.short_name.split(' ')[0],
            )

    class ModelAssignForm(ModelForm):
        divisions = MyModelChoiceField(widget=CheckboxSelectMultiple, queryset=Division.objects.filter(tournament=round.tournament).order_by('venue_group'))
        class Meta:
            model = Motion
            fields = ("divisions",)

    MotionFormSet = modelformset_factory(Motion, ModelAssignForm, extra=0, fields=['divisions'])

    if request.method == 'POST':
        formset = MotionFormSet(request.POST)
        formset.save() # Should be checking for validity but on a deadline and was buggy
        if 'submit' in request.POST:
            return redirect_round('motions', round)

    formset = MotionFormSet(queryset=Motion.objects.filter(round=round))
    return r2r(request, "motions_assign.html", dict(formset=formset))



@admin_required
@expect_post
@round_view
def release_motions(request, round):
    round.motions_released = True
    round.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_MOTIONS_RELEASE,
        user=request.user, round=round, tournament=round.tournament)

    return redirect_round('motions', round)

@admin_required
@expect_post
@round_view
def unrelease_motions(request, round):
    round.motions_released = False
    round.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_MOTIONS_UNRELEASE,
        user=request.user, round=round, tournament=round.tournament)

    return redirect_round('motions', round)

@admin_required
@expect_post
@round_view
def set_round_start_time(request, round):

    time_text = request.POST["start_time"]
    try:
        time = datetime.datetime.strptime(time_text, "%H:%M").time()
    except ValueError, e:
        print e
        return redirect_round('draw', round)

    round.starts_at = time
    round.save()

    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_ROUND_START_TIME_SET,
        user=request.user, round=round, tournament=round.tournament)

    return redirect_round('draw', round)

@admin_required
@expect_post
@tournament_view
def set_adj_test_score(request, t):

    try:
        adj_id = int(request.POST["adj_test_id"])
    except ValueError:
        return HttpResponseBadRequest("Score value is not legit")

    try:
        adjudicator = Adjudicator.objects.get(id=adj_id)
    except (Adjudicator.DoesNotExist, Adjudicator.MultipleObjectsReturned):
        return HttpResponseBadRequest("Adjudicator probably doesn't exist")

    # CONTINUE HERE CONTINUE HERE WORK IN PROGRESS
    score_text = request.POST["test_score"]
    try:
        score = float(score_text)
    except ValueError, e:
        print e
        return redirect_tournament('adj_feedback', t)

    adjudicator.test_score = score
    adjudicator.save()

    atsh = AdjudicatorTestScoreHistory(adjudicator=adjudicator,
        round=t.current_round, score=score)
    atsh.save()
    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_TEST_SCORE_EDIT,
        user=request.user, adjudicator_test_score_history=atsh, tournament=t)

    return redirect_tournament('adj_feedback', t)

@admin_required
@expect_post
@tournament_view
def set_adj_breaking_status(request, t):
    adj_id = int(request.POST["adj_id"])
    adj_breaking_status = str(request.POST["adj_breaking_status"])

    try:
        adjudicator = Adjudicator.objects.get(id=adj_id)
    except (Adjudicator.DoesNotExist, Adjudicator.MultipleObjectsReturned):
        return HttpResponseBadRequest("Adjudicator probably doesn't exist")

    if adj_breaking_status == "true":
        adjudicator.breaking = True
    else:
        adjudicator.breaking = False

    adjudicator.save()
    return HttpResponse("ok")

@admin_required
@expect_post
@tournament_view
def set_adj_note(request, t):

    try:
        adj_id = str(request.POST["adj_test_id"])
    except ValueError:
        return HttpResponseBadRequest("Note value is not legit")

    try:
        adjudicator = Adjudicator.objects.get(id=adj_id)
    except (Adjudicator.DoesNotExist, Adjudicator.MultipleObjectsReturned):
        return HttpResponseBadRequest("Adjudicator probably doesn't exist")

    # CONTINUE HERE CONTINUE HERE WORK IN PROGRESS
    note_text = request.POST["note"]
    try:
        note = str(note_text)
    except ValueError, e:
        print e
        return redirect_tournament('adj_feedback', t)

    adjudicator.notes = note
    adjudicator.save()

    return redirect_tournament('adj_feedback', t)

@login_required
@round_view
def results(request, round):

    draw = round.get_draw()
    stats = {
        'none': draw.filter(result_status=Debate.STATUS_NONE, ballot_in=False).count(),
        'ballot_in': draw.filter(result_status=Debate.STATUS_NONE, ballot_in=True).count(),
        'draft': draw.filter(result_status=Debate.STATUS_DRAFT).count(),
        'confirmed': draw.filter(result_status=Debate.STATUS_CONFIRMED).count(),
        'postponed': draw.filter(result_status=Debate.STATUS_POSTPONED).count(),
    }

    if not request.user.is_superuser:
        if round != request.tournament.current_round:
            raise Http404()
        template = "assistant/assistant_results.html"
        draw = draw.filter(result_status__in=(
            Debate.STATUS_NONE, Debate.STATUS_DRAFT, Debate.STATUS_POSTPONED))
    else:
        template = "results.html"

    num_motions = Motion.objects.filter(round=round).count()
    show_motions_column = num_motions > 1
    has_motions = num_motions > 0

    return r2r(request, template, dict(draw=draw, stats=stats,
        show_motions_column=show_motions_column, has_motions=has_motions)
    )

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_round_view('public_results')
def public_results(request, round):
    # Can't see results for current round or later
    if (round.seq >= round.tournament.current_round.seq and not round.tournament.release_all) or round.silent:
        print "Result page denied: round %d, current round %d, release all %s, silent %s" % (round.seq, round.tournament.current_round.seq, round.tournament.release_all, round.silent)
        raise Http404()
    draw = round.get_draw()
    show_motions_column = Motion.objects.filter(round=round).count() > 1 and round.tournament.config.get('show_motions_in_results')
    show_splits = round.tournament.config.get('show_splitting_adjudicators')
    show_ballots = round.tournament.config.get('ballots_released')
    return r2r(request, "public/public_results_for_round.html", dict(
            draw=draw, show_motions_column=show_motions_column, show_splits=show_splits,
            show_ballots=show_ballots))

@cache_page(PUBLIC_PAGE_CACHE_TIMEOUT)
@public_optional_tournament_view('public_results')
def public_results_index(request, tournament):
    rounds = Round.objects.filter(tournament=tournament,
        seq__lt=tournament.current_round.seq).order_by('seq')
    return r2r(request, "public/public_results_index.html", dict(rounds=rounds))

@login_required
@tournament_view
def edit_ballots(request, t, ballots_id):
    ballots = get_object_or_404(BallotSubmission, id=ballots_id)
    debate = ballots.debate

    if not request.user.is_superuser:
        template = 'assistant/assistant_enter_results.html'
        all_ballot_sets = debate.ballotsubmission_set_by_version_except_discarded
        disable_confirm = request.user == ballots.user and not t.config.get('enable_assistant_confirms')
    else:
        template = 'enter_results.html'
        all_ballot_sets = debate.ballotsubmission_set.order_by('version')
        disable_confirm = False

    identical_ballots_dict = debate.identical_ballots_dict
    for b in all_ballot_sets:
        if b in identical_ballots_dict:
            b.identical_ballot_versions = identical_ballots_dict[b]

    if request.method == 'POST':
        form = forms.BallotSetForm(ballots, request.POST)

        if form.is_valid():
            form.save()

            if ballots.discarded:
                action_type = ActionLog.ACTION_TYPE_BALLOT_DISCARD
            elif ballots.confirmed:
                action_type = ActionLog.ACTION_TYPE_BALLOT_CONFIRM
            else:
                action_type = ActionLog.ACTION_TYPE_BALLOT_EDIT
            ActionLog.objects.log(type=action_type, user=request.user,
                ballot_submission=ballots, ip_address=get_ip_address(request), tournament=t)

            return redirect_round('results', debate.round)
    else:
        form = forms.BallotSetForm(ballots)

    return r2r(request, template, dict(
        debate              =debate,
        form                =form,
        round               =debate.round,
        ballots             =ballots,
        all_ballot_sets     =all_ballot_sets,
        disable_confirm     =disable_confirm,
        new                 =False,
        ballot_not_singleton=all_ballot_sets.exclude(id=ballots_id).exists(),
        show_adj_contact    =True))

# Don't cache
@public_optional_tournament_view('public_ballots')
def public_new_ballots(request, t, adj_id):

    adjudicator = get_object_or_404(Adjudicator, id=adj_id)

    round = t.current_round
    if round.draw_status != Round.STATUS_RELEASED or not round.motions_released:
        return r2r(request, 'public/public_enter_results_error.html', dict(adjudicator=adjudicator, message='The draw and/or motions for the round haven\'t been released yet.'))
    try:
        da = DebateAdjudicator.objects.get(adjudicator=adjudicator, debate__round=round)
    except DebateAdjudicator.DoesNotExist:
        return r2r(request, 'public/public_enter_results_error.html', dict(adjudicator=adjudicator, message='It looks like you don\'t have a debate this round.'))

    debate = da.debate

    ip_address = get_ip_address(request)

    ballots = BallotSubmission(
        debate         = debate,
        submitter_type = BallotSubmission.SUBMITTER_PUBLIC,
        ip_address     = ip_address)

    existing_ballots = debate.ballotsubmission_set.exclude(discarded=True).count()

    if request.method == 'POST':
        form = forms.BallotSetForm(ballots, request.POST, password=True)

        if form.is_valid():
            form.save()

            ActionLog.objects.log(type=ActionLog.ACTION_TYPE_BALLOT_SUBMIT,
                    ballot_submission=ballots, ip_address=ip_address, tournament=t)
            return r2r(request, 'public/public_success.html', dict(success_kind="ballot"))

    else:
        form = forms.BallotSetForm(ballots, password=True)

    return r2r(request, 'public/public_enter_results.html', dict(
        debate          =debate,
        form            =form,
        round           =round,
        ballots         =ballots,
        adjudicator     =adjudicator,
        existing_ballots=existing_ballots,
        show_adj_contact=False))

@login_required
@tournament_view
def new_ballots(request, t, debate_id):
    debate = get_object_or_404(Debate, id=debate_id)
    ip_address = get_ip_address(request)

    ballots = BallotSubmission(
        debate        =debate,
        submitter_type=BallotSubmission.SUBMITTER_TABROOM,
        user          =request.user,
        ip_address    =ip_address)

    if not debate.adjudicators.has_chair:
        return HttpResponseBadRequest("Whoops! This debate doesn't have a chair, so you can't enter results for it.")

    if not request.user.is_superuser:
        template = 'assistant/assistant_enter_results.html'
        all_ballot_sets = debate.ballotsubmission_set.exclude(discarded=True).order_by('version')
    else:
        template = 'enter_results.html'
        all_ballot_sets = debate.ballotsubmission_set.order_by('version')

    if request.method == 'POST':
        form = forms.BallotSetForm(ballots, request.POST)

        if form.is_valid():
            form.save()

            ActionLog.objects.log(type=ActionLog.ACTION_TYPE_BALLOT_CREATE, user=request.user,
                    ballot_submission=ballots, ip_address=ip_address, tournament=t)

            return redirect_round('results', debate.round)

    else:
        form = forms.BallotSetForm(ballots)

    return r2r(request, template, dict(
        debate              =debate,
        form                =form,
        round               =debate.round,
        ballots             =ballots,
        all_ballot_sets     =all_ballot_sets,
        new                 =True,
        ballot_not_singleton=all_ballot_sets.exists(),
        show_adj_contact    =True))

@login_required
@tournament_view
@expect_post
def toggle_postponed(request, t):
    debate_id = request.POST.get('debate')
    debate = Debate.objects.get(pk=debate_id)
    if debate.result_status == debate.STATUS_POSTPONED:
        debate.result_status = debate.STATUS_NONE
    else:
        debate.result_status = debate.STATUS_POSTPONED

    print debate.result_status
    debate.save()
    return HttpResponse("ok")


def get_speaker_standings(rounds, round, results_override=False, only_novices=False, for_replies=False):
    last_substantive_position = round.tournament.LAST_SUBSTANTIVE_POSITION
    reply_position = round.tournament.REPLY_POSITION
    minimum_debates_needed = Round.objects.filter(stage=Round.STAGE_PRELIMINARY, tournament=round.tournament).count() - round.tournament.config.get('standings_missed_debates')

    if for_replies:
        speaker_scores = SpeakerScore.objects.select_related(
            'speaker','ballot_submission', 'debate_team__debate__round'
            ).filter(ballot_submission__confirmed=True, position=reply_position)
    else:
        speaker_scores = SpeakerScore.objects.select_related(
            'speaker','ballot_submission', 'debate_team__debate__round'
            ).filter(ballot_submission__confirmed=True, position__lte=last_substantive_position)

    if only_novices is True:
        speakers = list(Speaker.objects.filter(team__tournament=round.tournament, novice=True).select_related(
            'team', 'team__institution', 'team__tournament'))
    else:
        speakers = list(Speaker.objects.filter(team__tournament=round.tournament).select_related(
            'team', 'team__institution', 'team__tournament'))

    def get_scores(speaker, this_speakers_scores):
        speaker_scores = [None] * len(rounds)
        for r in rounds:
            finding_score = next((x for x in this_speakers_scores if x.debate_team.debate.round == r), None)
            if finding_score:
                speaker_scores[r.seq - 1] = finding_score.score

        return speaker_scores

    for speaker in speakers:
        this_speakers_scores = [score for score in speaker_scores if score.speaker == speaker]
        speaker.scores = get_scores(speaker, this_speakers_scores)
        speaker.results_in = speaker.scores[-1] is not None or round.stage != Round.STAGE_PRELIMINARY or results_override
        if len(filter(None, speaker.scores)) > minimum_debates_needed:
            speaker.total = sum(filter(None, speaker.scores))
            speaker.average = sum(filter(None, speaker.scores)) / len(filter(None, speaker.scores))
        else:
            speaker.total = None
            speaker.average = None

        if for_replies:
            speaker.replies_given = len(filter(None, speaker.scores))

    if for_replies:
        speakers = [s for s in speakers if s.replies_given > 0]

    prev_total = None
    current_rank = 0

    if round.tournament.config.get('standings_method') is False:
        method = False
        speakers.sort(key=lambda x: x.average, reverse=True)
    else:
        method = True
        speakers.sort(key=lambda x: x.total, reverse=True)

    for i, speaker in enumerate(speakers, start=1):
        if method:
            comparison = speaker.total
        else:
            comparison = speaker.average

        if comparison != prev_total:
            current_rank = i
            prev_total = comparison
        speaker.rank = current_rank

    return speakers


@admin_required
@round_view
def team_standings(request, round, for_print=False):
    from debate.models import TeamScore
    teams = Team.objects.ranked_standings(round)

    rounds = round.tournament.prelim_rounds(until=round).order_by('seq')
    team_scores = list(TeamScore.objects.select_related('debate_team__team', 'debate_team__debate__round').filter(ballot_submission__confirmed=True))

    def get_round_result(team, r):
        try:
            ts = next((x for x in team_scores if x.debate_team.team == team and x.debate_team.debate.round == r), None)
            try:
                ts.opposition = ts.debate_team.opposition.team # TODO: this slows down the page generation considerably
            except:
                pass
            return ts
        except TeamScore.DoesNotExist:
            return None

    for team in teams:
        team.results_in = round.stage != Round.STAGE_PRELIMINARY or get_round_result(team, round) is not None
        team.round_results = [get_round_result(team, r) for r in rounds]
        team.wins = [ts.win for ts in team.round_results if ts].count(True)
        team.points = sum([ts.points for ts in team.round_results if ts])
        if round.tournament.config.get('show_avg_margin'):
            try:
                margins = []
                for ts in team.round_results:
                    if ts:
                        if ts.get_margin is not None:
                            margins.append(ts.get_margin)

                team.avg_margin = sum(margins) / float(len(margins))
            except ZeroDivisionError:
                team.avg_margin = None

    show_draw_strength = decide_show_draw_strength(round.tournament)

    return r2r(request, 'team_standings.html', dict(teams=teams, rounds=rounds, for_print=for_print,
       show_ballots=False, show_draw_strength=show_draw_strength))

@admin_required
@round_view
def speaker_standings(request, round, for_print=False):
    rounds = round.tournament.prelim_rounds(until=round).order_by('seq')
    speakers = get_speaker_standings(rounds, round)
    return r2r(request, "speaker_standings.html", dict(speakers=speakers,
                                        rounds=rounds, for_print=for_print))

@cache_page(TAB_PAGES_CACHE_TIMEOUT)
@public_optional_tournament_view('tab_released')
def public_speaker_tab(request, t):
    round = t.current_round
    rounds = t.prelim_rounds(until=round).order_by('seq')
    speakers = get_speaker_standings(rounds, round)
    return r2r(request, 'public/public_speaker_tab.html', dict(speakers=speakers,
            rounds=rounds, round=round))

@admin_required
@round_view
def novice_standings(request, round, for_print=False):
    rounds = round.tournament.prelim_rounds(until=round).order_by('seq')
    speakers = get_speaker_standings(rounds, round, only_novices=True)
    return r2r(request, "novice_standings.html", dict(speakers=speakers,
                                        rounds=rounds))


@cache_page(TAB_PAGES_CACHE_TIMEOUT)
@public_optional_tournament_view('tab_released')
def public_novices_tab(request, t):
    round = t.current_round
    rounds = round.tournament.prelim_rounds(until=round).order_by('seq')
    speakers = get_speaker_standings(rounds, round, only_novices=True)
    return r2r(request, 'public/public_novices_tab.html', dict(speakers=speakers,
            rounds=rounds, round=round))

@admin_required
@round_view
def reply_standings(request, round, for_print=False):
    rounds = round.tournament.prelim_rounds(until=round).order_by('seq')
    speakers = get_speaker_standings(rounds, round, for_replies=True)
    return r2r(request, 'reply_standings.html', dict(speakers=speakers,
                                        rounds=rounds, for_print=for_print))

@cache_page(TAB_PAGES_CACHE_TIMEOUT)
@public_optional_tournament_view('tab_released')
def public_replies_tab(request, t):
    round = t.current_round
    rounds = t.prelim_rounds(until=round).order_by('seq')
    speakers = get_speaker_standings(rounds, round, for_replies=True)
    return r2r(request, 'public/public_reply_tab.html', dict(speakers=speakers,
            rounds=rounds, round=round))


@admin_required
@round_view
def draw_matchups_edit(request, round):
    draw = round.get_draw_with_standings(round)
    debates = len(draw)
    unused_teams = round.unused_teams()
    possible_debates = int(len(unused_teams) / 2) + 1 # The blank rows to add
    possible_debates = [None] * possible_debates
    return r2r(request, "draw_matchups_edit.html", dict(draw=draw,
        possible_debates=possible_debates,unused_teams=unused_teams))

@admin_required
@expect_post
@round_view
def save_matchups(request, round):
    #print request.POST.keys()

    existing_debate_ids = [int(a.replace('debate_', '')) for a in request.POST.keys() if a.startswith('debate_')]
    for debate_id in existing_debate_ids:
        debate = Debate.objects.get(id=debate_id)
        new_aff_id = request.POST.get('aff_%s' % debate_id).replace('team_', '')
        new_neg_id = request.POST.get('neg_%s' % debate_id).replace('team_', '')

        if new_aff_id and new_neg_id:
            DebateTeam.objects.filter(debate=debate).delete()
            debate.save()

            new_aff_team = Team.objects.get(id=int(new_aff_id))
            new_aff_dt = DebateTeam(debate=debate, team=new_aff_team, position=DebateTeam.POSITION_AFFIRMATIVE)
            new_aff_dt.save()

            new_aff_team = Team.objects.get(id=int(new_neg_id))
            new_neg_dt = DebateTeam(debate=debate, team=new_aff_team, position=DebateTeam.POSITION_NEGATIVE)
            new_neg_dt.save()
        else:
            # If there's blank debates we need to delete those
            debate.delete()

    new_debate_ids = [int(a.replace('new_debate_', '')) for a in request.POST.keys() if a.startswith('new_debate_')]
    for debate_id in new_debate_ids:
        new_aff_id = request.POST.get('aff_%s' % debate_id).replace('team_', '')
        new_neg_id = request.POST.get('neg_%s' % debate_id).replace('team_', '')

        if new_aff_id and new_neg_id:
            debate = Debate(round=round, venue=None)
            debate.save()

            aff_team = Team.objects.get(id=int(new_aff_id))
            neg_team = Team.objects.get(id=int(new_neg_id))
            new_aff_dt = DebateTeam(debate=debate, team=aff_team, position=DebateTeam.POSITION_AFFIRMATIVE)
            new_neg_dt = DebateTeam(debate=debate, team=neg_team, position=DebateTeam.POSITION_NEGATIVE)
            new_aff_dt.save()
            new_neg_dt.save()

    return HttpResponse("ok")

@admin_required
@round_view
def draw_venues_edit(request, round):

    draw = round.get_draw()
    return r2r(request, "draw_venues_edit.html", dict(draw=draw))


@admin_required
@expect_post
@round_view
def save_venues(request, round):

    def v_id(a):
        try:
            return int(request.POST[a].split('_')[1])
        except IndexError:
            return None
    data = [(int(a.split('_')[1]), v_id(a))
             for a in request.POST.keys()]

    debates = Debate.objects.in_bulk([d_id for d_id, _ in data])
    venues = Venue.objects.in_bulk([v_id for _, v_id in data])
    for debate_id, venue_id in data:
        if venue_id == None:
            debates[debate_id].venue = None
        else:
            debates[debate_id].venue = venues[venue_id]

        debates[debate_id].save()

    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_VENUES_SAVE,
        user=request.user, round=round, tournament=round.tournament)

    return HttpResponse("ok")


@admin_required
@round_view
def draw_adjudicators_edit(request, round):
    draw = round.get_draw()
    adj0 = Adjudicator.objects.first()
    duplicate_adjs = round.tournament.config.get('duplicate_adjs')

    def calculate_prior_adj_genders(team):
        debates = team.get_debates(round.seq)
        adjs = DebateAdjudicator.objects.filter(debate__in=debates).count()
        male_adjs = DebateAdjudicator.objects.filter(debate__in=debates,adjudicator__gender="M").count()
        if male_adjs > 0:
            male_adj_percent = int((float(male_adjs) / float(adjs)) * 100)
            return male_adj_percent
        else:
            return 0

    for debate in draw:
        aff_male_adj_percent = calculate_prior_adj_genders(debate.aff_team)
        debate.aff_team.male_adj_percent = aff_male_adj_percent

        neg_male_adj_percent = calculate_prior_adj_genders(debate.neg_team)
        debate.neg_team.male_adj_percent = neg_male_adj_percent

        if neg_male_adj_percent > aff_male_adj_percent:
            debate.gender_class = (neg_male_adj_percent / 5) - 10
        else:
            debate.gender_class = (aff_male_adj_percent / 5) - 10

    return r2r(request, "draw_adjudicators_edit.html", dict(
        draw=draw, adj0=adj0, duplicate_adjs=duplicate_adjs))

def _json_adj_allocation(debates, unused_adj):

    obj = {}

    def _adj(a):
        return {
            'id': a.id,
            'name': a.name + " (" + a.institution.short_code + ")",
            'is_unaccredited': a.is_unaccredited,
            'gender': a.gender
        }

    def _debate(d):
        r = {}
        if d.adjudicators.chair:
            r['chair'] = _adj(d.adjudicators.chair)
        r['panel'] = [_adj(a) for a in d.adjudicators.panel]
        r['trainees'] = [_adj(a) for a in d.adjudicators.trainees]
        return r

    obj['debates'] = dict((d.id, _debate(d)) for d in debates)
    obj['unused'] = [_adj(a) for a in unused_adj]

    return HttpResponse(json.dumps(obj))


@admin_required
@round_view
def draw_adjudicators_get(request, round):
    draw = round.get_draw()

    return _json_adj_allocation(draw, round.unused_adjudicators())


@admin_required
@round_view
def save_adjudicators(request, round):
    if request.method != "POST":
        return HttpResponseBadRequest("Expected POST")

    def id(s):
        s = s.replace('[]', '')
        return int(s.split('_')[1])

    debate_ids = set(id(a) for a in request.POST);
    debates = Debate.objects.in_bulk(list(debate_ids));
    debate_adjudicators = {}
    for d_id, debate in debates.items():
        a = debate.adjudicators
        a.delete()
        debate_adjudicators[d_id] = a

    for key, vals in request.POST.lists():
        if key.startswith("chair_"):
            debate_adjudicators[id(key)].chair = vals[0]
        if key.startswith("panel_"):
            for val in vals:
                debate_adjudicators[id(key)].panel.append(val)
        if key.startswith("trainees_"):
            for val in vals:
                debate_adjudicators[id(key)].trainees.append(val)

    # We don't do any validity checking here, so that the adjudication
    # core can save a work in progress.

    for d_id, alloc in debate_adjudicators.items():
        alloc.save()

    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_ADJUDICATORS_SAVE,
        user=request.user, round=round, tournament=round.tournament)

    return HttpResponse("ok")


@admin_required
@round_view
def adj_conflicts(request, round):

    data = {
        'personal': {},
        'history': {},
        'institutional': {},
    }

    def add(type, adj_id, target_id):
        if adj_id not in data[type]:
            data[type][adj_id] = []
        data[type][adj_id].append(target_id)

    for ac in AdjudicatorConflict.objects.all():
        add('personal', ac.adjudicator_id, ac.team_id)

    for ic in AdjudicatorInstitutionConflict.objects.all():
        for team in Team.objects.filter(institution=ic.institution):
            add('institutional', ic.adjudicator_id, team.id)

    history = DebateAdjudicator.objects.filter(
        debate__round__seq__lt = round.seq,
    )

    for da in history:
        add('history', da.adjudicator_id, da.debate.aff_team.id)
        add('history', da.adjudicator_id, da.debate.neg_team.id)

    return HttpResponse(json.dumps(data), content_type="text/json")


@login_required
@round_view
def master_sheets_list(request, round):
    venue_groups = VenueGroup.objects.all()
    return r2r(request, 'master_sheets_list.html', dict(venue_groups=venue_groups))


@login_required
@round_view
def master_sheets_view(request, round, venue_group_id):
    # Temporary - pre unified venue groups
    base_venue_group = VenueGroup.objects.get(id=venue_group_id)
    active_tournaments = Tournament.objects.filter(active=True)

    for tournament in list(active_tournaments):
        tournament.debates = Debate.objects.select_related(
            'division','division__venue_group__short_name','round','round__tournament','aff_team','neg_team'
        ).filter(
            # All Debates, with a matching round, at the same venue group name
            round__seq=round.seq,
            round__tournament=tournament,
            division__venue_group__short_name=base_venue_group.short_name # hack - remove when venue groups are unified
        ).order_by('round','division__venue_group__short_name','division')

    return r2r(request, 'master_sheets_view.html', dict(
        base_venue_group=base_venue_group,
        active_tournaments=active_tournaments
    ))


@admin_required
@tournament_view
def adj_scores(request, t):
    data = {}

    #TODO: make round-dependent
    for adj in Adjudicator.objects.all():
        data[adj.id] = adj.score

    return HttpResponse(json.dumps(data), content_type="text/json")


@login_required
@tournament_view
def adj_feedback(request, t):

    if not t.config.get('share_adjs'):
        adjudicators = Adjudicator.objects.select_related('institution').filter(tournament=t)
    else:
        adjudicators = Adjudicator.objects.select_related('institution').all()

    if not request.user.is_superuser:
        template = 'assistant/assistant_adjudicator_feedback.html'
    else:
        template = 'adjudicator_feedback.html'

        from debate.models import SpeakerScoreByAdj
        all_adjs_rooms = DebateAdjudicator.objects.select_related('adjudicator').all()
        all_adjs_scores = SpeakerScoreByAdj.objects.select_related('debate_adjudicator','ballot_submission').all()
        for adj in adjudicators:
            adjs_rooms  = all_adjs_rooms.filter(adjudicator = adj)
            adj.debates = len(adjs_rooms)

            adjs_scores = all_adjs_scores.filter(debate_adjudicator = adjs_rooms)
            if len(adjs_scores) > 0:
                adj.avg_score = sum(s.score for s in adjs_scores) / len(adjs_scores)

                ballot_ids = []
                ballot_margins = []
                for score in adjs_scores:
                    ballot_ids.append(score.ballot_submission)

                ballot_ids = sorted(set([b.id for b in ballot_ids])) # Deduplication of ballot IDS

                for ballot_id in ballot_ids:
                    # For each unique ballot id, total its scores
                    single_round = adjs_scores.filter(ballot_submission = ballot_id)
                    scores = [s.score for s in single_round] # TODO this is slow - should be prefetched
                    slice_end = len(scores)
                    teamA = sum(scores[:len(scores)/2])
                    teamB = sum(scores[len(scores)/2:])
                    ballot_margins.append(max(teamA, teamB) - min(teamA, teamB))

                adj.avg_margin = sum(ballot_margins) / len(ballot_margins)

            else:
                adj.avg_score = None
                adj.avg_margin = None

    return r2r(request, template, dict(adjudicators=adjudicators))


@login_required
@tournament_view
def get_adj_feedback(request, t):

    adj = get_object_or_404(Adjudicator, pk=int(request.GET['id']))
    feedback = adj.get_feedback()
    data = [ [
              unicode(f.round.abbreviation),
              unicode(str(f.version) + (f.confirmed and "*" or "")),
              f.debate.bracket,
              f.debate.matchup,
              unicode(f.source),
              f.score,
              {None: "Unsure", True: "Yes", False: "No"}[f.agree_with_decision],
              f.comments,
              f.confirmed,
             ] for f in feedback ]

    return HttpResponse(json.dumps({'aaData': data}), content_type="text/json")


# Don't cache
@public_optional_tournament_view('public_feedback')
def public_enter_feedback_adjudicator(request, t, adj_id):

    source = get_object_or_404(Adjudicator, id=adj_id)
    include_panellists = request.tournament.config.get('panellist_feedback_enabled') > 0
    ip_address = get_ip_address(request)
    source_name = source.name

    submission_fields = {
        'submitter_type': AdjudicatorFeedback.SUBMITTER_PUBLIC,
        'ip_address'    : ip_address
    }

    if request.method == "POST":
        form = forms.make_feedback_form_class_for_public_adj(source, submission_fields, include_panellists=include_panellists)(request.POST)
        if form.is_valid():
            adj_feedback = form.save()
            ActionLog.objects.log(type=ActionLog.ACTION_TYPE_FEEDBACK_SUBMIT,
                    ip_address=ip_address, adjudicator_feedback=adj_feedback, tournament=t)
            return r2r(request, 'public/public_success.html', dict(success_kind="feedback"))
    else:
        form = forms.make_feedback_form_class_for_public_adj(source, submission_fields, include_panellists=include_panellists)()

    return r2r(request, 'public/public_enter_feedback_adj.html', dict(source_name=source_name, form=form))

# Don't cache
@public_optional_tournament_view('public_feedback')
def public_enter_feedback_team(request, t, team_id):

    source = get_object_or_404(Team, id=team_id)
    ip_address = get_ip_address(request)
    source_name = source.short_name

    submission_fields = {
        'submitter_type': AdjudicatorFeedback.SUBMITTER_PUBLIC,
        'ip_address'    : ip_address
    }

    if request.method == "POST":
        form = forms.make_feedback_form_class_for_public_team(source, submission_fields)(request.POST)
        if form.is_valid():
            adj_feedback = form.save()
            ActionLog.objects.log(type=ActionLog.ACTION_TYPE_FEEDBACK_SUBMIT,
                    ip_address=ip_address, adjudicator_feedback=adj_feedback, tournament=t)
            return r2r(request, 'public/public_success.html', dict(success_kind="feedback"))
    else:
        form = forms.make_feedback_form_class_for_public_team(source, submission_fields)()

    return r2r(request, 'public/public_enter_feedback_team.html', dict(source_name=source_name, form=form))

@login_required
@tournament_view
def enter_feedback(request, t, adj_id):

    adj = get_object_or_404(Adjudicator, id=adj_id)
    ip_address = get_ip_address(request)

    submission_fields = {
        'submitter_type': AdjudicatorFeedback.SUBMITTER_TABROOM,
        'user'          : request.user,
        'ip_address'    : ip_address
    }

    if request.method == "POST":
        form = forms.make_feedback_form_class_for_tabroom(adj, submission_fields)(request.POST)
        if form.is_valid():
            adj_feedback = form.save()
            ActionLog.objects.log(type=ActionLog.ACTION_TYPE_FEEDBACK_SAVE,
                user=request.user, adjudicator_feedback=adj_feedback, tournament=t)
            return redirect_tournament('adj_feedback', t)
    else:
        form = forms.make_feedback_form_class_for_tabroom(adj, submission_fields)()

    return r2r(request, 'enter_feedback.html', dict(adj=adj, form=form))

@admin_required
@round_view
def ballot_checkin(request, round):
    ballots_left = ballot_checkin_number_left(round)
    return r2r(request, 'ballot_checkin.html', dict(ballots_left=ballots_left))

class DebateBallotCheckinError(Exception):
    pass

def get_debate_from_ballot_checkin_request(request, round):
    # Called by the submit button on the ballot checkin form.
    # Returns the message that should go in the "success" field.
    v = request.POST.get('venue')

    try:
        venue = Venue.objects.get(name__iexact=v)
    except Venue.DoesNotExist:
        raise DebateBallotCheckinError('There aren\'t any venues with the name "' + v + '".')

    try:
        debate = Debate.objects.get(round=round, venue=venue)
    except Debate.DoesNotExist:
        raise DebateBallotCheckinError('There wasn\'t a debate in venue ' + venue.name + ' this round.')

    if debate.ballot_in:
        raise DebateBallotCheckinError('The ballot for venue ' + venue.name + ' has already been checked in.')

    return debate

def ballot_checkin_number_left(round):
    count = Debate.objects.filter(round=round, ballot_in=False).count()
    return count

@admin_required
@round_view
def ballot_checkin_get_details(request, round):
    try:
        debate = get_debate_from_ballot_checkin_request(request, round)
    except DebateBallotCheckinError, e:
        data = {'exists': False, 'message': str(e)}
        return HttpResponse(json.dumps(data))

    obj = dict()

    obj['exists'] = True
    obj['venue'] = debate.venue.name
    obj['aff_team'] = debate.aff_team.short_name
    obj['neg_team'] = debate.neg_team.short_name

    adjs = debate.adjudicators
    adj_names = [adj.name for type, adj in adjs if type != DebateAdjudicator.TYPE_TRAINEE]
    obj['num_adjs'] = len(adj_names)
    obj['adjudicators'] = adj_names

    obj['ballots_left'] = ballot_checkin_number_left(round)

    return HttpResponse(json.dumps(obj))

@admin_required
@round_view
def post_ballot_checkin(request, round):
    try:
        debate = get_debate_from_ballot_checkin_request(request, round)
    except DebateBallotCheckinError, e:
        data = {'exists': False, 'message': str(e)}
        return HttpResponse(json.dumps(data))

    debate.ballot_in = True
    debate.save()

    ActionLog.objects.log(type=ActionLog.ACTION_TYPE_BALLOT_CHECKIN,
            user=request.user, debate=debate, tournament=round.tournament)

    obj = dict()

    obj['success'] = True
    obj['venue'] = debate.venue.name
    obj['debate_description'] = debate.aff_team.short_name + " vs " + debate.neg_team.short_name

    obj['ballots_left'] = ballot_checkin_number_left(round)

    return HttpResponse(json.dumps(obj))
