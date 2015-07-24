"""Module to compute the teams breaking in a BreakCategory."""

from collections import Counter
from standings import annotate_team_standings
from models import BreakingTeam

def get_teams_breaking(category, include_all=False, include_categories=False):
    """Returns a list of Teams, with additional attributes. For each Team t in
    the returned list:
        t.rank is the rank of the team, including ineligible teams.
        t.break_rank is the rank of the team out of those that are in the break.
    'category' must be a BreakCategory instance.

    If 'include_all' is True:
      - Teams that would break but for the institution cap are included in the
        returned list, with t.break_rank set to the string "(capped)".
      - If category.is_general is True, then teams that would break but for
        being ineligible for this category are included in the returned list,
        but with t.break_rank set to the string "(ineligible)".
    Note that in no circumstances are teams that broke in a higher priority
    category included in the list of returned teams.

    If 'include_all' is False, the capped and ineligible teams are excluded,
    but t.rank is still the rank including those teams.

    If 'include_categories' is True, t.categories_for_display will be a comma-
    delimited list of category names that are not this category, and lower
    or equal priority to this category.
    """
    teams = category.teams_breaking.all()
    if not include_all:
        teams = teams.filter(break_rank__isnull=False)
    teams = annotate_team_standings(teams, tournament=category.tournament)
    for team in teams:
        bt = team.breakingteam_set.get(break_category=category)
        team.rank = bt.rank
        if bt.break_rank is None:
            if bt.remark:
                team.break_rank = "(" + bt.get_remark_display().lower() + ")"
            else:
                team.break_rank = "<error>"
        else:
            team.break_rank = bt.break_rank

        if include_categories:
            categories = team.break_categories_nongeneral.exclude(id=category.id).exclude(priority__lt=category.priority)
            team.categories_for_display = "(" + ", ".join(c.name for c in categories) + ")" if categories else ""
        else:
            team.categories_for_display = ""

    return teams

def generate_all_teams_breaking(tournament):
    """Deletes all breaking teams information, then generates breaking teams
    from scratch according to update_teams_breaking()."""
    for category in tournament.breakcategory_set.all():
        category.breakingteam_set.all().delete()
    update_all_teams_breaking(tournament)

def update_all_teams_breaking(tournament):
    """Runs update_teams_breaking for all categories, taking taking break
    category priorities into account appropriately.
    """
    teams_broken_higher_priority = set()
    teams_broken_cur_priority = set()
    cur_priority = None

    for category in tournament.breakcategory_set.order_by('priority'):

        # If this is a new priority level, reset the current list
        if cur_priority != category.priority:
            teams_broken_higher_priority |= teams_broken_cur_priority
            teams_broken_cur_priority = set()
            cur_priority = category.priority

        eligible_teams = _eligible_team_set(category)
        this_break = _generate_teams_breaking(category, eligible_teams, teams_broken_higher_priority)
        teams_broken_cur_priority.update(this_break)

def update_teams_breaking(category):
    """Computes the breaking teams and stores them in the database as
    BreakingTeam objects. Each BreakingTeam bt has:
        bt.rank set to the rank of the team, including ineligible teams
        bt.break_rank set to the rank of the team out of those that are in the
            break, or None if the team is ineligible
        bt.remark set to
            - BreakingTeam.REMARK_CAPPED if the team would break but for the
              institution cap, or
            - BreakingTeam.REMARK_INELIGIBLE if category.is_general is True and
              the team would break but for being ineligible for this category.
            - BreakingTeam.REMARK_DIFFERENT_BREAK if the team broke in a
              different category.

    If a breaking team entry already exists and there is a remark associated
    with it, it retains the remark and skips that team.
    """
    higher_breakingteams = BreakingTeam.objects.filter(break_category__priority__lt=category.priority, break_rank__isnull=False).select_related('team')
    higher_teams = {bt.team for bt in higher_breakingteams}
    eligible_teams = _eligible_team_set(category)
    _generate_teams_breaking(category, eligible_teams, higher_teams)

def _eligible_team_set(category):
    if category.is_general:
        return category.tournament.team_set # all in tournament
    else:
        return category.team_set

def _generate_teams_breaking(category, eligible_teams, teams_broken_higher_priority=set()):
    """Generates a list of breaking teams for the given category and returns
    a list of teams in the (actual) break, i.e. excluding teams that are
    ineligible, capped, broke in a different break, and so on."""

    eligible_teams = annotate_team_standings(eligible_teams, tournament=category.tournament)

    prev_rank_value = (None, None) # (points, speaks)
    cur_rank = 0
    cur_break_rank = 0 # actual break rank
    cur_break_seq = 0  # sequential count of breaking teams

    teams_breaking = list()
    breakingteams_all = list()
    breakingteams_to_create = list()
    breakingteams_to_save = list()

    # Variables for institutional caps and non-breaking teams
    num_teams_from_institution = Counter()

    # Do initial allocation of ranks and break ranks
    for i, team in enumerate(eligible_teams, start=1):

        try:
            bt = BreakingTeam.objects.get(break_category=category, team=team)
            existing = True
        except BreakingTeam.DoesNotExist:
            bt = BreakingTeam(break_category=category, team=team)
            existing = False

        # Compute overall rank
        rank_value = (team.points, team.speaker_score)
        is_new_rank = rank_value != prev_rank_value
        if is_new_rank:
            # if we have enough teams, we're done
            if len(teams_breaking) >= category.break_size:
                break
            # under AIDA 2016 rules if we've gone past five wins, we're done
            if category.institution_cap_rule == category.INSTITUTION_CAP_RULE_AIDA_2016 \
                    and team.points < 5:
                break
            cur_rank = i
            prev_rank_value = rank_value
        bt.rank = cur_rank

        # If there is an existing remark, scrub the break rank and skip
        if existing and bt.remark:
            bt.break_rank = None

        # Check if ineligible
        elif not team.break_categories.filter(pk=category.pk).exists():
            bt.remark = bt.REMARK_INELIGIBLE

        # Check if capped out by institution cap
        elif _capped_out(category, num_teams_from_institution[team.institution], cur_rank):
            bt.remark = bt.REMARK_CAPPED

        # Check if already broken to a higher category
        elif team in teams_broken_higher_priority:
            bt.remark = bt.REMARK_DIFFERENT_BREAK

        # If neither, this team is in the break
        else:
            # Compute break rank
            cur_break_seq += 1
            if is_new_rank:
                cur_break_rank = cur_break_seq
            bt.break_rank = cur_break_rank

            teams_breaking.append(team)

        if existing:
            breakingteams_to_save.append(bt)
        else:
            breakingteams_to_create.append(bt)
        breakingteams_all.append(bt)

        # Take note of the institution
        num_teams_from_institution[team.institution] += 1

    # Bring back capped out teams if necessary to fill break
    num_teams_to_cap_in = category.break_size - len(teams_breaking)
    num_teams_capped_in = 0
    cur_rank = 0
    for bt in breakingteams_all:
        is_new_rank = bt.rank != cur_rank



        num_teams_to_cap_in -=
        if num_teams_to_cap_in == 0:
            break

    #
    if len(teams_breaking) < category.break_size:
        for i, bt in enumerate(capped_breakingteams):
            if i >= category.break_size - len(teams_breaking):
                bt.remark = None

    # Clean and save to database
    assert set(breakingteams_all) == set(breakingteams_to_save + breakingteams_to_create)
    for bt in breakingteams_all:
        bt.full_clean()
    for bt in breakingteams_to_save:
        bt.save()
    BreakingTeam.objects.bulk_create(breakingteams_to_create)
    BreakingTeam.objects.filter(break_category=category, break_rank__isnull=False).exclude(
        team_id__in=[t.id for t in teams_breaking]).delete()

    return teams_breaking

def _capped_out(bt, category, num_teams_from_institution):
    if category.institution_cap_rule == category.INSTITUTION_CAP_RULE_NONE:
        return False

    elif category.institution_cap_rule == category.INSTITUTION_CAP_RULE_AIDA_PRE_2015:
        return num_teams_from_institution >= category.institution_cap

    elif category.institution_cap_rule == category.INSTITUTION_CAP_RULE_AIDA_2016:
        if cur_rank <= category.break_size:
            return num_teams_from_institution >= category.institution_cap
        else:
            return num_teams_from_institution >= 1